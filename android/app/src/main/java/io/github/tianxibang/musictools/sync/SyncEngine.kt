package io.github.tianxibang.musictools.sync

import android.content.Context
import android.media.MediaScannerConnection
import io.github.tianxibang.musictools.data.PairedPc
import io.github.tianxibang.musictools.data.Prefs
import io.github.tianxibang.musictools.net.InboxItem
import io.github.tianxibang.musictools.net.SyncClient
import io.github.tianxibang.musictools.net.SyncException
import io.github.tianxibang.musictools.net.discoverPcs
import io.github.tianxibang.musictools.net.toHex
import kotlinx.coroutines.currentCoroutineContext
import kotlinx.coroutines.delay
import kotlinx.coroutines.ensureActive
import kotlinx.coroutines.flow.first
import kotlinx.coroutines.withTimeoutOrNull
import kotlinx.serialization.Serializable
import kotlinx.serialization.json.Json
import java.io.File
import java.io.IOException
import java.security.MessageDigest

/** Files the mirror manages; anything else in the music folder (e.g. a FLAC you put there) is never deleted. */
private val MIRRORED = setOf("opus", "ogg", "m3u", "m3u8")

@Serializable
data class Pending(val path: String, val id: String, val sha256: String, val size: Long, val mtime: Long)

@Serializable
data class Failed(val path: String, val size: Long, val mtime: Long, val error: String)

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

    fun summary(): String {
        val parts = buildList {
            if (sent > 0) add("$sent sent to PC")
            if (duplicates > 0) add("$duplicates already in library")
            if (flacsRemoved > 0) add("$flacsRemoved FLAC${if (flacsRemoved == 1) "" else "s"} replaced by Opus")
            if (downloaded > 0) add("$downloaded downloaded")
            if (deleted > 0) add("$deleted removed")
            if (waiting > 0) add("$waiting still converting on the PC")
            if (failed > 0) add("$failed failed")
        }
        return if (parts.isEmpty()) "Everything is up to date" else parts.joinToString(" · ")
    }
}

class SyncEngine(private val context: Context, private val log: SyncLog) {
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
    }

    suspend fun run(): SyncResult {
        val pc = prefs.pc ?: throw SyncException(0, "Not paired with a PC yet")
        val result = SyncResult()
        log.status("Looking for ${pc.name}…")
        val client = connect(pc)
        log.line("Connected to ${pc.name} at ${client.host}", Level.OK)
        try {
            sendNewFlacs(client, result)
            finishPending(client, result)
            mirror(client, result)
        } finally {
            if (scan.isNotEmpty()) {
                // Let music players see the changes without waiting for Android's own scan
                MediaScannerConnection.scanFile(context, scan.toTypedArray(), null, null)
                scan.clear()
            }
        }
        return result
    }

    /** Last known addresses first, then whatever the network announces for this PC's id. */
    private suspend fun connect(pc: PairedPc): SyncClient {
        val s = prefs.settings
        fun client(host: String, port: Int) = SyncClient(host, port, prefs.deviceId, pc.fingerprint, pc.token)
        for (host in pc.hosts) {
            currentCoroutineContext().ensureActive()
            val c = client(host, pc.port)
            try {
                c.info()
                return c
            } catch (e: SyncException) {
                throw e  // reachable but refused (e.g. certificate changed): don't try elsewhere
            } catch (_: IOException) {
            }
        }
        val found = withTimeoutOrNull(6000) {
            discoverPcs(context).first { list -> list.any { it.id == pc.id } }.first { it.id == pc.id }
        } ?: throw IOException("Couldn't reach ${pc.name}. Is Music Tools running with sync turned on, " +
            "and is the phone on the same network${if (s.wifiOnly) " (Wi-Fi)" else ""}?")
        val c = client(found.host, found.port)
        c.info()
        prefs.rememberHost(found.host, found.port)
        return c
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
                    val retry = fail == null || fail.size != f.length() || fail.mtime != f.lastModified()
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
                savePending(store)
                result.sent++
                log.line("Sent ${f.name}", Level.OK)
            } catch (e: SyncException) {
                result.failed++
                store = store.copy(failed = store.failed + Failed(f.absolutePath, size, mtime, e.message ?: "error"))
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
                    store = store.copy(items = store.items - p,
                        failed = store.failed + Failed(p.path, p.size, p.mtime, item.detail))
                } else {
                    if (item.state == "duplicate") result.duplicates++
                    val dest = safeChild(musicDir, item.opusPath)
                        ?: throw SyncException(0, "The PC sent an invalid file path")
                    log.status("Downloading ${dest.name}")
                    val size = client.manifest().files.firstOrNull { it.path == item.opusPath }?.size ?: -1
                    client.download(item.opusPath, dest, size)
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
        val files = client.manifest().files
        if (files.isEmpty()) {
            log.line("The PC's Opus folder is empty; not touching the phone's music.", Level.WARN)
            return
        }
        musicDir.mkdirs()
        val wanted = HashMap<String, Long>()
        val todo = files.mapNotNull { rf ->
            val dest = safeChild(musicDir, rf.path) ?: return@mapNotNull null
            wanted[dest.canonicalPath] = rf.size
            if (!dest.exists() || dest.length() != rf.size) rf to dest else null
        }
        val extras = if (s.keepExtraFiles) emptyList() else musicDir.walkTopDown()
            .filter { it.isFile && it.extension.lowercase() in MIRRORED && it.canonicalPath !in wanted }
            .toList()

        todo.forEachIndexed { i, (rf, dest) ->
            currentCoroutineContext().ensureActive()
            log.status("Downloading ${dest.name}", i, todo.size)
            try {
                client.download(rf.path, dest, rf.size) { got -> log.progress(got, rf.size) }
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

    companion object {
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
