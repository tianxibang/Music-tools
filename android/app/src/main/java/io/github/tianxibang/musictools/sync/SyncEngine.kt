package io.github.tianxibang.musictools.sync

import android.content.Context
import android.media.MediaScannerConnection
import io.github.tianxibang.musictools.data.LibraryCache
import io.github.tianxibang.musictools.data.LibrarySnapshot
import io.github.tianxibang.musictools.data.Prefs
import io.github.tianxibang.musictools.data.Selection
import io.github.tianxibang.musictools.net.Connection
import io.github.tianxibang.musictools.net.InboxItem
import io.github.tianxibang.musictools.net.SyncClient
import io.github.tianxibang.musictools.net.SyncException
import io.github.tianxibang.musictools.net.toHex
import kotlinx.coroutines.currentCoroutineContext
import kotlinx.coroutines.delay
import kotlinx.coroutines.ensureActive
import kotlinx.serialization.Serializable
import kotlinx.serialization.json.Json
import java.io.File
import java.security.MessageDigest

/** Songs the PC sends: Opus, plus lossy files it keeps as they are (its Lossy folder). */
val SONG_EXTENSIONS = setOf("opus", "ogg", "mp3", "m4a", "aac", "wma")

/** Files the mirror manages; anything else in the music folder (e.g. a FLAC you put there) is never deleted. */
private val MIRRORED = SONG_EXTENSIONS + setOf("m3u", "m3u8", "lrc")

fun isSong(path: String) = path.substringAfterLast('.', "").lowercase() in SONG_EXTENSIONS

/** A song's lyrics file (song.lrc next to song.opus). */
fun isLyrics(path: String) = path.substringAfterLast('.', "").equals("lrc", true)

@Serializable
data class Pending(val path: String, val id: String, val sha256: String, val size: Long, val mtime: Long)

/** A FLAC that couldn't be synced. Retried on the next syncs, and always by "Sync now". */
@Serializable
data class Failed(val path: String, val size: Long, val mtime: Long, val error: String, val attempts: Int = 1) {
    val name get() = path.substringAfterLast('/')
}

/** Automatic syncs give up on a file after this many failures in a row; "Sync now" still tries. */
const val AUTO_RETRIES = 3

@Serializable
data class PendingStore(val items: List<Pending> = emptyList(), val failed: List<Failed> = emptyList())

class SyncResult {
    var sent = 0
    var duplicates = 0
    var flacsRemoved = 0
    var downloaded = 0
    var deleted = 0
    var failed = 0
    var waiting = 0
    /** Files that failed before and still aren't synced (possibly not retried this time). */
    var stillFailing = 0

    fun summary(): String {
        val parts = buildList {
            if (sent > 0) add("$sent sent to PC")
            if (duplicates > 0) add("$duplicates already in library")
            if (flacsRemoved > 0) add("$flacsRemoved FLAC${if (flacsRemoved == 1) "" else "s"} replaced by Opus")
            if (downloaded > 0) add("$downloaded downloaded")
            if (deleted > 0) add("$deleted removed")
            if (waiting > 0) add("$waiting still converting on the PC")
            if (failed > 0) add("$failed failed")
            else if (stillFailing > 0) add("$stillFailing couldn't sync")
        }
        return if (parts.isEmpty()) "Everything is up to date" else parts.joinToString(" · ")
    }
}

class SyncEngine(private val context: Context, private val log: SyncLog, private val retryFailed: Boolean = false) {
    private val prefs = Prefs.get(context)
    private val json = Json { ignoreUnknownKeys = true }
    private val pendingFile = prefs.pendingFile(context)
    private val scan = mutableListOf<String>()

    private fun loadPending(): PendingStore =
        runCatching { json.decodeFromString<PendingStore>(pendingFile.readText()) }.getOrDefault(PendingStore())

    private fun savePending(store: PendingStore) {
        val tmp = File(pendingFile.parentFile, pendingFile.name + ".tmp")
        tmp.writeText(json.encodeToString(PendingStore.serializer(), store))
        tmp.renameTo(pendingFile)
        log.setFailed(store.failed)
    }

    /** Record a failure, counting repeats for the same unchanged file. */
    private fun PendingStore.withFailure(path: String, size: Long, mtime: Long, error: String): PendingStore {
        val old = failed.firstOrNull { it.path == path && it.size == size && it.mtime == mtime }
        return copy(failed = failed.filter { it.path != path } +
            Failed(path, size, mtime, error, (old?.attempts ?: 0) + 1))
    }

    private fun PendingStore.withoutFailure(path: String) = copy(failed = failed.filter { it.path != path })

    suspend fun run(): SyncResult {
        val pc = prefs.pc ?: throw SyncException(0, "Not paired with a PC yet")
        val result = SyncResult()
        log.setFailed(loadPending().failed)
        log.status("Looking for ${pc.name}…")
        val client = Connection.open(context)
        log.line("Connected to ${pc.name} " + if (client.host == USB_HOST) "by USB cable" else "at ${client.host}", Level.OK)
        try {
            sendNewFlacs(client, result)
            finishPending(client, result)
            mirror(client, result)
            result.stillFailing = loadPending().failed.size
        } finally {
            if (scan.isNotEmpty()) {
                // Let music players see the changes without waiting for Android's own scan
                MediaScannerConnection.scanFile(context, scan.toTypedArray(), null, null)
                scan.clear()
            }
        }
        return result
    }

    // --- phone -> PC

    private fun findNewFlacs(store: PendingStore): List<File> {
        val s = prefs.settings
        val musicDir = File(s.musicDir).canonicalFile
        val busy = store.items.map { it.path }.toSet()
        val failed = store.failed.associateBy { it.path }
        val out = mutableListOf<File>()
        for (dir in s.sourceDirs.map(::File).filter(File::isDirectory)) {
            dir.walkTopDown()
                .onEnter { d -> !d.name.startsWith(".") && d.canonicalFile != musicDir && d.name != "Android" }
                .filter { it.isFile && it.extension.equals("flac", true) && !it.name.startsWith(".") }
                .forEach { f ->
                    val fail = failed[f.absolutePath]
                    val changed = fail == null || fail.size != f.length() || fail.mtime != f.lastModified()
                    val retry = changed || retryFailed || fail!!.attempts < AUTO_RETRIES
                    if (f.absolutePath !in busy && retry) out += f
                }
        }
        return out.distinctBy { it.canonicalPath }
    }

    private suspend fun sendNewFlacs(client: SyncClient, result: SyncResult) {
        var store = loadPending()
        val flacs = findNewFlacs(store)
        if (flacs.isEmpty()) return
        log.line("${flacs.size} new FLAC${if (flacs.size == 1) "" else "s"} to send to the PC")
        // Forget old failures for files that changed or disappeared
        store = store.copy(failed = store.failed.filter { f -> File(f.path).let { it.exists() && it.length() == f.size } })
        flacs.forEachIndexed { i, f ->
            currentCoroutineContext().ensureActive()
            log.status("Sending ${f.name}", i, flacs.size)
            val size = f.length()
            val mtime = f.lastModified()
            try {
                val sha = sha256(f)
                val item = client.upload(f, sha) { sent -> log.progress(sent, size) }
                store = store.copy(items = store.items + Pending(f.absolutePath, item.id, sha, size, mtime))
                    .withoutFailure(f.absolutePath)
                savePending(store)
                result.sent++
                log.line("Sent ${f.name}", Level.OK)
            } catch (e: SyncException) {
                result.failed++
                store = store.withFailure(f.absolutePath, size, mtime, e.message ?: "error")
                savePending(store)
                log.line("Couldn't send ${f.name}: ${e.message}", Level.ERROR)
            }
        }
    }

    /** Wait for the PC to convert what we sent, fetch the Opus, then remove the FLAC from the phone. */
    private suspend fun finishPending(client: SyncClient, result: SyncResult) {
        var store = loadPending()
        if (store.items.isEmpty()) return
        val musicDir = File(prefs.settings.musicDir)
        val deadline = System.currentTimeMillis() + 10 * 60_000
        val remaining = store.items.toMutableList()
        while (remaining.isNotEmpty() && System.currentTimeMillis() < deadline) {
            currentCoroutineContext().ensureActive()
            log.status("Waiting for the PC to convert ${remaining.size} file${if (remaining.size == 1) "" else "s"}…")
            for (p in remaining.toList()) {
                val item: InboxItem = try {
                    client.inboxItem(p.id)
                } catch (e: SyncException) {
                    if (e.status == 404) {  // the PC lost it (e.g. reinstalled): send it again next time
                        remaining -= p
                        store = store.copy(items = store.items - p)
                        savePending(store)
                    }
                    continue
                }
                if (!item.finished) continue
                remaining -= p
                if (item.state == "failed") {
                    result.failed++
                    log.line("The PC couldn't import ${item.name}: ${item.detail}", Level.ERROR)
                    store = store.copy(items = store.items - p).withFailure(p.path, p.size, p.mtime, item.detail)
                } else {
                    if (item.state == "duplicate") result.duplicates++
                    val dest = safeChild(musicDir, item.opusPath)
                        ?: throw SyncException(0, "The PC sent an invalid file path")
                    log.status("Downloading ${dest.name}")
                    val size = client.manifest().files.firstOrNull { it.path == item.opusPath }?.size ?: -1
                    client.download(item.opusPath, dest, size)
                    prefs.addSentFromPhone(item.opusPath)
                    scan += dest.absolutePath
                    result.downloaded++
                    removeFlac(p, result)
                    store = store.copy(items = store.items - p)
                    log.line("${item.name} → ${item.opusPath}", Level.OK)
                }
                savePending(store)
                runCatching { client.acknowledge(p.id) }
            }
            if (remaining.isNotEmpty()) delay(2000)
        }
        result.waiting += remaining.size
    }

    private fun removeFlac(p: Pending, result: SyncResult) {
        if (!prefs.settings.deleteFlacAfterSync) return
        val f = File(p.path)
        // Only delete the exact file we sent: if it was edited or replaced since, keep it
        if (f.exists() && f.length() == p.size && f.lastModified() == p.mtime && sha256(f) == p.sha256) {
            if (f.delete()) {
                scan += f.absolutePath
                result.flacsRemoved++
            }
        }
    }

    // --- PC -> phone

    private suspend fun mirror(client: SyncClient, result: SyncResult) {
        val s = prefs.settings
        val musicDir = File(s.musicDir)
        log.status("Comparing music with the PC…")
        val all = client.manifest().files
        if (all.isEmpty()) {
            log.line("The PC's Opus folder is empty; not touching the phone's music.", Level.WARN)
            return
        }
        val snap = refreshLibrary(client)
        val files = if (s.syncAll || snap == null) {
            if (!s.syncAll) log.line("This PC can't tell which songs belong to your selection " +
                "(update Music Tools on the PC); syncing everything.", Level.WARN)
            all
        } else {
            val keep = Selection.wantedPaths(snap, s.selection, prefs.sentFromPhone)
            if (keep.isEmpty()) {
                log.line("Nothing is selected to sync. Pick some music in the Library tab.", Level.WARN)
                return
            }
            // Other files (e.g. cover images) are tiny and kept; playlists only when selected, lyrics with their song
            val keptSongs = keep.mapTo(HashSet()) { it.substringBeforeLast('.') }
            all.filter { f ->
                f.path in keep || if (isLyrics(f.path)) f.path.substringBeforeLast('.') in keptSongs
                else !isSong(f.path) && !f.path.startsWith("Playlists/")
            }
        }
        musicDir.mkdirs()
        val wanted = HashMap<String, Long>()
        val todo = files.mapNotNull { rf ->
            val dest = safeChild(musicDir, rf.path) ?: return@mapNotNull null
            wanted[dest.canonicalPath] = rf.size
            // A lyrics edit (a nudged timing) often keeps the size, so those compare times too
            val stale = isLyrics(rf.path) && rf.mtime != 0L && dest.lastModified() / 1000 != rf.mtime
            if (!dest.exists() || dest.length() != rf.size || stale) rf to dest else null
        }
        val extras = if (s.keepExtraFiles) emptyList() else musicDir.walkTopDown()
            .filter { it.isFile && it.extension.lowercase() in MIRRORED && it.canonicalPath !in wanted }
            .toList()

        todo.forEachIndexed { i, (rf, dest) ->
            currentCoroutineContext().ensureActive()
            log.status("Downloading ${dest.name}", i, todo.size)
            try {
                client.download(rf.path, dest, rf.size) { got -> log.progress(got, rf.size) }
                if (isLyrics(rf.path) && rf.mtime != 0L) dest.setLastModified(rf.mtime * 1000)
                scan += dest.absolutePath
                result.downloaded++
            } catch (e: SyncException) {
                result.failed++
                log.line("Couldn't download ${rf.path}: ${e.message}", Level.ERROR)
            }
        }
        for (f in extras) {
            if (f.delete()) {
                scan += f.absolutePath
                result.deleted++
                log.line("Removed ${f.relativeTo(musicDir)}", Level.WARN)
            }
        }
        if (extras.isNotEmpty()) {
            musicDir.walkBottomUp().filter { it.isDirectory && it != musicDir && it.list()?.isEmpty() == true }
                .forEach { it.delete() }
        }
        // Leftovers of interrupted downloads
        musicDir.walkTopDown().filter { it.isFile && it.name.startsWith(".") && it.name.endsWith(".part") }
            .forEach { it.delete() }
    }

    /** Fetch the track list and playlists for stats and selection; null if the PC is too old to send them. */
    private fun refreshLibrary(client: SyncClient): LibrarySnapshot? = try {
        LibraryCache.get(context).update(client.library(), client.playlists())
        LibraryCache.get(context).state.value
    } catch (e: SyncException) {
        if (e.status != 404) throw e
        null
    }

    companion object {
        const val USB_HOST = "127.0.0.1"

        fun storedFailures(context: Context): List<Failed> = runCatching {
            Json { ignoreUnknownKeys = true }.decodeFromString<PendingStore>(
                Prefs.get(context).pendingFile(context).readText()).failed
        }.getOrDefault(emptyList())

        /** [root]/[rel], or null if the path tries to leave [root]. The PC is trusted, but not that much. */
        fun safeChild(root: File, rel: String): File? {
            if (rel.isEmpty() || rel.startsWith("/") || rel.contains('\\') || rel.contains('\u0000')) return null
            val base = root.canonicalFile
            val f = File(base, rel).canonicalFile
            return if (f.path.startsWith(base.path + File.separator)) f else null
        }

        fun sha256(f: File): String {
            val md = MessageDigest.getInstance("SHA-256")
            f.inputStream().use { input ->
                val buf = ByteArray(1 shl 16)
                while (true) {
                    val n = input.read(buf)
                    if (n < 0) break
                    md.update(buf, 0, n)
                }
            }
            return md.digest().toHex()
        }
    }
}
