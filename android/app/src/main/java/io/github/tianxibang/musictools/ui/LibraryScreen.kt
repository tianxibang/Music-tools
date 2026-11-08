package io.github.tianxibang.musictools.ui

import android.os.StatFs
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.ExpandLess
import androidx.compose.material.icons.filled.ExpandMore
import androidx.compose.material.icons.filled.Search
import androidx.compose.material3.Button
import androidx.compose.material3.Icon
import androidx.compose.material3.LinearProgressIndicator
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.SegmentedButton
import androidx.compose.material3.SegmentedButtonDefaults
import androidx.compose.material3.SingleChoiceSegmentedButtonRow
import androidx.compose.material3.Text
import androidx.compose.material3.TriStateCheckbox
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.saveable.rememberSaveable
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.state.ToggleableState
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import androidx.lifecycle.compose.collectAsStateWithLifecycle
import io.github.tianxibang.musictools.data.LibraryCache
import io.github.tianxibang.musictools.data.Prefs
import io.github.tianxibang.musictools.data.Selection
import io.github.tianxibang.musictools.net.Track
import io.github.tianxibang.musictools.sync.SyncLog
import io.github.tianxibang.musictools.sync.SyncWorker
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext
import java.io.File

private data class Album(val artist: String, val name: String, val tracks: List<Track>) {
    val size get() = tracks.sumOf { it.size }
}

private data class Artist(val name: String, val albums: List<Album>) {
    val tracks get() = albums.sumOf { it.tracks.size }
    val size get() = albums.sumOf { it.size }
}

/** What's on the phone, measured from the files themselves. */
private data class PhoneStats(val paths: Set<String>, val bytes: Long, val free: Long, val total: Long)

@Composable
fun LibraryScreen() {
    val context = LocalContext.current
    val prefsStore = remember { Prefs.get(context) }
    val prefs by prefsStore.state.collectAsStateWithLifecycle()
    val snap by remember { LibraryCache.get(context) }.state.collectAsStateWithLifecycle()
    val sync by remember { SyncLog.get(context) }.state.collectAsStateWithLifecycle()
    val s = prefs.settings

    var phone by remember { mutableStateOf<PhoneStats?>(null) }
    LaunchedEffect(sync.lastRun, s.musicDir) {
        phone = withContext(Dispatchers.IO) {
            val root = File(s.musicDir)
            val files = root.walkTopDown().filter { it.isFile && it.extension.equals("opus", true) }.toList()
            val fs = runCatching { StatFs(root.takeIf { it.exists() }?.path ?: "/storage/emulated/0") }.getOrNull()
            PhoneStats(files.map { it.relativeTo(root).invariantSeparatorsPath }.toSet(), files.sumOf { it.length() },
                fs?.availableBytes ?: 0, fs?.totalBytes ?: 0)
        }
    }
    val artists = remember(snap.updated) {
        snap.library.tracks.groupBy { it.albumArtist }.map { (name, tracks) ->
            Artist(name, tracks.groupBy { it.album }.map { (album, t) -> Album(name, album, t.sortedBy { it.trackNo }) }
                .sortedBy { it.name.lowercase() })
        }.sortedBy { it.name.lowercase() }
    }
    val onPhone = remember(snap.updated, phone) {
        phone?.let { p -> snap.library.tracks.filter { it.path in p.paths } } ?: emptyList()
    }

    var query by rememberSaveable { mutableStateOf("") }
    var expanded by remember { mutableStateOf(setOf<String>()) }
    fun setSelection(sel: Set<String>) = prefsStore.update { it.copy(selection = sel) }

    LazyColumn(
        Modifier.fillMaxSize().padding(horizontal = 20.dp),
        verticalArrangement = Arrangement.spacedBy(14.dp),
    ) {
        item { PageTitle("Library") }

        item {
            Section("On this phone") {
                val p = phone
                if (p == null) Hint("Counting…") else {
                    Row(horizontalArrangement = Arrangement.spacedBy(10.dp)) {
                        Stat("Songs", "%,d".format(p.paths.size), Modifier.weight(1f))
                        Stat("Music", bytes(p.bytes), Modifier.weight(1f))
                        Stat("Play time", duration(onPhone.sumOf { it.duration }), Modifier.weight(1f))
                    }
                    if (p.total > 0) {
                        val used = 1f - p.free.toFloat() / p.total
                        LinearProgressIndicator({ used }, Modifier.fillMaxWidth())
                        Hint("${bytes(p.free)} free of ${bytes(p.total)}")
                    }
                }
            }
        }

        if (snap.library.tracks.isNotEmpty()) {
            item {
                val sum = snap.library.summary
                Section("On your PC") {
                    Row(horizontalArrangement = Arrangement.spacedBy(10.dp)) {
                        Stat("FLAC songs", "%,d".format(sum.flacCount), Modifier.weight(1f))
                        Stat("Lossless", bytes(sum.flacBytes), Modifier.weight(1f))
                        Stat("Opus", bytes(sum.opusBytes), Modifier.weight(1f))
                    }
                    if (sum.pending > 0) Hint("${sum.pending} waiting to be converted (run the pipeline in the PC tab)")
                }
            }
            val pool = onPhone.ifEmpty { snap.library.tracks }
            val label = if (onPhone.isEmpty()) " (PC)" else ""
            item { Bars("Top artists$label", pool.groupingBy { it.albumArtist }.eachCount()) }
            item { Bars("Genres$label", pool.mapNotNull { it.genre }.groupingBy { it }.eachCount()) }
            item {
                Bars("Decades$label", pool.mapNotNull { it.year }.groupingBy { "${it / 10 * 10}s" }.eachCount(),
                    sortByKey = true)
            }
            item {
                Bars("Quality$label", pool.groupingBy {
                    when {
                        it.bitrate >= 192 -> "192 kbps and up"
                        it.bitrate >= 128 -> "128–191 kbps"
                        it.bitrate > 0 -> "under 128 kbps"
                        else -> "unknown"
                    }
                }.eachCount())
            }
        }

        item {
            Section("Choose what to sync") {
                SingleChoiceSegmentedButtonRow(Modifier.fillMaxWidth()) {
                    SegmentedButton(s.syncAll, { prefsStore.update { it.copy(syncAll = true) } },
                        SegmentedButtonDefaults.itemShape(0, 2)) { Text("Everything") }
                    SegmentedButton(!s.syncAll, { prefsStore.update { it.copy(syncAll = false) } },
                        SegmentedButtonDefaults.itemShape(1, 2)) { Text("Only what I pick") }
                }
                if (s.syncAll) {
                    Hint("The whole Opus library goes on the phone.")
                } else if (snap.library.tracks.isEmpty()) {
                    Hint("Sync once with your PC to load the list of artists and albums.")
                } else {
                    val wanted = Selection.wantedPaths(snap, s.selection, prefsStore.sentFromPhone)
                    val size = wanted.sumOf { snap.byPath[it]?.size ?: 0 }
                    val free = (phone?.free ?: 0) + (phone?.bytes ?: 0)
                    Text("${wanted.count { it.endsWith(".opus") }} songs selected · ${bytes(size)}",
                        color = if (free in 1 until size) Palette.Err else Palette.Text)
                    Hint(if (free in 1 until size) "That's more than fits on the phone." else
                        "Songs you don't pick are removed from the phone on the next sync (never from the PC). " +
                            "Songs you sent from the phone always stay.")
                    OutlinedTextField(query, { query = it }, Modifier.fillMaxWidth(), singleLine = true,
                        leadingIcon = { Icon(Icons.Default.Search, null) }, placeholder = { Text("Search") })
                }
            }
        }

        if (!s.syncAll && snap.library.tracks.isNotEmpty()) {
            val q = query.trim().lowercase()
            val pls = snap.playlists.filter { q.isEmpty() || q in it.name.lowercase() }
            if (pls.isNotEmpty()) {
                item { Text("Playlists", style = MaterialTheme.typography.titleSmall, color = Palette.Muted) }
                items(pls, key = { "pl:" + it.name }) { pl ->
                    val key = Selection.playlist(pl.name)
                    CheckRow(if (key in s.selection) ToggleableState.On else ToggleableState.Off, pl.name,
                        "${pl.tracks.size} songs") {
                        setSelection(if (key in s.selection) s.selection - key else s.selection + key)
                    }
                }
            }
            val shown = artists.filter { a ->
                q.isEmpty() || q in a.name.lowercase() || a.albums.any { q in it.name.lowercase() }
            }
            item { Text("Artists", style = MaterialTheme.typography.titleSmall, color = Palette.Muted) }
            items(shown, key = { "ar:" + it.name }) { a ->
                val artistKey = Selection.artist(a.name)
                val albumKeys = a.albums.map { Selection.album(a.name, it.name) }
                val state = when {
                    artistKey in s.selection || (albumKeys.isNotEmpty() && albumKeys.all { it in s.selection }) ->
                        ToggleableState.On
                    albumKeys.any { it in s.selection } -> ToggleableState.Indeterminate
                    else -> ToggleableState.Off
                }
                Column {
                    CheckRow(state, a.name, "${a.albums.size} album${if (a.albums.size == 1) "" else "s"} · " +
                        "${a.tracks} songs · ${bytes(a.size)}",
                        trailing = {
                            Icon(if (a.name in expanded) Icons.Default.ExpandLess else Icons.Default.ExpandMore, null,
                                Modifier.clickable { expanded = if (a.name in expanded) expanded - a.name else expanded + a.name }
                                    .padding(8.dp))
                        }) {
                        // Ticking an artist covers all their albums, now and in the future
                        val without = s.selection - artistKey - albumKeys.toSet()
                        setSelection(if (state == ToggleableState.On) without else without + artistKey)
                    }
                    if (a.name in expanded || (q.isNotEmpty() && q !in a.name.lowercase())) {
                        a.albums.filter { q.isEmpty() || q in a.name.lowercase() || q in it.name.lowercase() }
                            .forEach { al ->
                                val key = Selection.album(a.name, al.name)
                                val on = artistKey in s.selection || key in s.selection
                                Row(Modifier.padding(start = 28.dp)) {
                                    CheckRow(if (on) ToggleableState.On else ToggleableState.Off, al.name,
                                        "${al.tracks.size} songs · ${bytes(al.size)}") {
                                        val sel = if (artistKey in s.selection) {
                                            // Turn "the whole artist" into "every album but this one"
                                            s.selection - artistKey + albumKeys.filter { it != key }
                                        } else if (on) s.selection - key else s.selection + key
                                        setSelection(sel)
                                    }
                                }
                            }
                    }
                }
            }
            item {
                Row(Modifier.fillMaxWidth().padding(vertical = 4.dp), horizontalArrangement = Arrangement.End) {
                    Button(onClick = { SyncWorker.syncNow(context) }) { Text("Sync now to apply") }
                }
            }
        }
        item { Spacer(Modifier.height(12.dp)) }
    }
}

@Composable
private fun CheckRow(state: ToggleableState, title: String, subtitle: String,
                     trailing: @Composable () -> Unit = {}, onToggle: () -> Unit) {
    Row(Modifier.fillMaxWidth().clickable(onClick = onToggle), verticalAlignment = Alignment.CenterVertically) {
        TriStateCheckbox(state, onClick = onToggle)
        Column(Modifier.weight(1f)) {
            Text(title, maxLines = 1, overflow = TextOverflow.Ellipsis)
            Hint(subtitle)
        }
        trailing()
    }
}

@Composable
private fun Stat(label: String, value: String, modifier: Modifier = Modifier) {
    Column(modifier) {
        Text(value, style = MaterialTheme.typography.titleLarge, fontWeight = FontWeight.SemiBold)
        Hint(label)
    }
}

@Composable
private fun Bars(title: String, counts: Map<String, Int>, sortByKey: Boolean = false, top: Int = 8) {
    if (counts.isEmpty()) return
    val rows = if (sortByKey) counts.entries.sortedBy { it.key } else counts.entries.sortedByDescending { it.value }.take(top)
    val max = rows.maxOf { it.value }.toFloat()
    Section(title) {
        rows.forEach { (name, n) ->
            Column(verticalArrangement = Arrangement.spacedBy(3.dp)) {
                Row {
                    Text(name, Modifier.weight(1f), maxLines = 1, overflow = TextOverflow.Ellipsis,
                        style = MaterialTheme.typography.bodyMedium)
                    Spacer(Modifier.width(8.dp))
                    Hint("$n")
                }
                LinearProgressIndicator({ n / max }, Modifier.fillMaxWidth(), color = Palette.AccentHi,
                    trackColor = Palette.Raised, drawStopIndicator = {})
            }
        }
    }
}

fun bytes(n: Long): String = when {
    n >= 1L shl 30 -> "%.1f GB".format(n / (1L shl 30).toDouble())
    n >= 1L shl 20 -> "%.0f MB".format(n / (1L shl 20).toDouble())
    else -> "%.0f KB".format(n / 1024.0)
}

fun duration(seconds: Double): String {
    val m = (seconds / 60).toLong()
    return if (m >= 60 * 24) "${m / (60 * 24)} d ${m / 60 % 24} h" else if (m >= 60) "${m / 60} h ${m % 60} min" else "$m min"
}
