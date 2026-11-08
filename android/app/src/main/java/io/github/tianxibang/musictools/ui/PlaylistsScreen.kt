package io.github.tianxibang.musictools.ui

import androidx.activity.compose.BackHandler
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.heightIn
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.itemsIndexed
import androidx.compose.foundation.lazy.items
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.automirrored.filled.ArrowBack
import androidx.compose.material.icons.automirrored.filled.QueueMusic
import androidx.compose.material.icons.filled.Add
import androidx.compose.material.icons.filled.Check
import androidx.compose.material.icons.filled.Close
import androidx.compose.material.icons.filled.Delete
import androidx.compose.material.icons.filled.Edit
import androidx.compose.material.icons.filled.KeyboardArrowDown
import androidx.compose.material.icons.filled.KeyboardArrowUp
import androidx.compose.material.icons.filled.Search
import androidx.compose.material3.AlertDialog
import androidx.compose.material3.Button
import androidx.compose.material3.CircularProgressIndicator
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedButton
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateListOf
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberCoroutineScope
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import androidx.lifecycle.compose.collectAsStateWithLifecycle
import io.github.tianxibang.musictools.data.LibraryCache
import io.github.tianxibang.musictools.data.LibrarySnapshot
import io.github.tianxibang.musictools.net.Connection
import io.github.tianxibang.musictools.net.Playlist
import io.github.tianxibang.musictools.net.Track
import io.github.tianxibang.musictools.net.cleanPlaylistName
import kotlinx.coroutines.launch

/** A playlist being edited; [original] is null for a new one. */
private class Draft(val original: Playlist?, name: String, tracks: List<String>) {
    var name by mutableStateOf(name)
    val tracks = mutableStateListOf<String>().apply { addAll(tracks) }
    val dirty get() = original == null || name != original.name || tracks.toList() != original.tracks
}

@Composable
fun PlaylistsScreen() {
    val context = LocalContext.current
    val cache = remember { LibraryCache.get(context) }
    val snap by cache.state.collectAsStateWithLifecycle()
    val scope = rememberCoroutineScope()
    var loading by remember { mutableStateOf(false) }
    var error by remember { mutableStateOf<String?>(null) }
    var draft by remember { mutableStateOf<Draft?>(null) }
    var naming by remember { mutableStateOf<Draft?>(null) }  // asking for a name (new or rename)

    fun refresh() {
        loading = true
        scope.launch {
            error = runCatching {
                Connection.use(context) { c ->
                    val pls = c.playlists()
                    val lib = if (cache.state.value.library.tracks.isEmpty()) c.library() else null
                    cache.update(lib, pls)
                }
            }.exceptionOrNull()?.message
            loading = false
        }
    }
    LaunchedEffect(Unit) { refresh() }

    fun save(d: Draft, onDone: () -> Unit) {
        loading = true
        scope.launch {
            error = runCatching {
                Connection.use(context) { c ->
                    c.savePlaylist(d.name, d.tracks.toList())
                    if (d.original != null && d.original.name != d.name) c.deletePlaylist(d.original.name)
                    cache.update(playlists = c.playlists())
                }
            }.exceptionOrNull()?.message
            loading = false
            if (error == null) onDone()
        }
    }

    fun delete(name: String) {
        loading = true
        scope.launch {
            error = runCatching {
                Connection.use(context) { c ->
                    c.deletePlaylist(name)
                    cache.update(playlists = c.playlists())
                }
            }.exceptionOrNull()?.message
            loading = false
            if (error == null) draft = null
        }
    }

    val d = draft
    if (d != null) {
        BackHandler { draft = null }
        Editor(d, snap, loading, error, onBack = { draft = null; error = null }, onRename = { naming = d },
            onSave = { save(d) { draft = null } }, onDelete = { d.original?.let { delete(it.name) } })
    } else {
        LazyColumn(
            Modifier.fillMaxSize().padding(horizontal = 20.dp),
            verticalArrangement = Arrangement.spacedBy(10.dp),
        ) {
            item { PageTitle("Playlists", "Saved on your PC and synced to every device.") }
            item {
                Row(verticalAlignment = Alignment.CenterVertically) {
                    Button(onClick = { naming = Draft(null, "", emptyList()) }) {
                        Icon(Icons.Default.Add, null)
                        Spacer(Modifier.width(6.dp))
                        Text("New playlist")
                    }
                    Spacer(Modifier.width(12.dp))
                    if (loading) CircularProgressIndicator(Modifier.size(20.dp), strokeWidth = 2.dp)
                }
            }
            error?.let { item { Hint("$it Showing the playlists from the last sync.", Palette.Err) } }
            if (snap.playlists.isEmpty() && !loading) item { Hint("No playlists yet.") }
            items(snap.playlists, key = { it.name }) { pl ->
                Section(modifier = Modifier.clickable { draft = Draft(pl, pl.name, pl.tracks) }) {
                    Row(verticalAlignment = Alignment.CenterVertically) {
                        Icon(Icons.AutoMirrored.Filled.QueueMusic, null, tint = Palette.AccentHi)
                        Spacer(Modifier.width(12.dp))
                        Column(Modifier.weight(1f)) {
                            Text(pl.name)
                            Hint("${pl.tracks.size} songs · " + duration(pl.tracks.sumOf { snap.byPath[it]?.duration ?: 0.0 }) +
                                if (pl.missing > 0) " · ${pl.missing} not converted yet" else "")
                        }
                    }
                }
            }
            item { Spacer(Modifier.height(12.dp)) }
        }
    }

    naming?.let { n ->
        NameDialog(n.name, existing = snap.playlists.map { it.name }.filter { it != n.original?.name },
            onDismiss = { naming = null }) { name ->
            n.name = name
            naming = null
            if (draft == null) draft = n
        }
    }
}

@Composable
private fun Editor(d: Draft, snap: LibrarySnapshot, busy: Boolean, error: String?, onBack: () -> Unit,
                   onRename: () -> Unit, onSave: () -> Unit, onDelete: () -> Unit) {
    var adding by remember { mutableStateOf(false) }
    var confirmDelete by remember { mutableStateOf(false) }
    LazyColumn(Modifier.fillMaxSize().padding(horizontal = 12.dp), verticalArrangement = Arrangement.spacedBy(4.dp)) {
        item {
            Row(Modifier.padding(top = 8.dp), verticalAlignment = Alignment.CenterVertically) {
                IconButton(onClick = onBack) { Icon(Icons.AutoMirrored.Filled.ArrowBack, "Back") }
                Column(Modifier.weight(1f).clickable(onClick = onRename)) {
                    PageTitle(d.name.ifEmpty { "New playlist" })
                }
                IconButton(onClick = onRename) { Icon(Icons.Default.Edit, "Rename") }
            }
        }
        item {
            Row(horizontalArrangement = Arrangement.spacedBy(8.dp), verticalAlignment = Alignment.CenterVertically) {
                OutlinedButton(onClick = { adding = true }) {
                    Icon(Icons.Default.Add, null)
                    Spacer(Modifier.width(6.dp))
                    Text("Add songs")
                }
                Button(onClick = onSave, enabled = d.dirty && d.name.isNotBlank() && !busy) {
                    Icon(Icons.Default.Check, null)
                    Spacer(Modifier.width(6.dp))
                    Text("Save")
                }
                if (busy) CircularProgressIndicator(Modifier.size(20.dp), strokeWidth = 2.dp)
            }
        }
        error?.let { item { Hint(it, Palette.Err) } }
        if (d.tracks.isEmpty()) item { Hint("No songs yet. Tap “Add songs”.") }
        itemsIndexed(d.tracks, key = { i, p -> "$i:$p" }) { i, path ->
            val t = snap.byPath[path]
            Row(verticalAlignment = Alignment.CenterVertically) {
                Column(Modifier.weight(1f).padding(start = 8.dp)) {
                    Text(t?.title ?: path.substringAfterLast('/'), maxLines = 1, overflow = TextOverflow.Ellipsis)
                    Hint(t?.let { "${it.artist} · ${it.album}" } ?: path, )
                }
                IconButton(onClick = { if (i > 0) d.tracks.add(i - 1, d.tracks.removeAt(i)) }, enabled = i > 0) {
                    Icon(Icons.Default.KeyboardArrowUp, "Move up")
                }
                IconButton(onClick = { if (i < d.tracks.lastIndex) d.tracks.add(i + 1, d.tracks.removeAt(i)) },
                    enabled = i < d.tracks.lastIndex) {
                    Icon(Icons.Default.KeyboardArrowDown, "Move down")
                }
                IconButton(onClick = { d.tracks.removeAt(i) }) { Icon(Icons.Default.Close, "Remove") }
            }
        }
        if (d.original != null) {
            item {
                TextButton(onClick = { confirmDelete = true }, Modifier.padding(top = 12.dp)) {
                    Icon(Icons.Default.Delete, null, tint = Palette.Err)
                    Spacer(Modifier.width(6.dp))
                    Text("Delete playlist", color = Palette.Err)
                }
            }
        }
        item { Spacer(Modifier.height(16.dp)) }
    }
    if (adding) AddSongs(snap.library.tracks, d.tracks.toSet(), onDismiss = { adding = false }) { d.tracks.add(it.path) }
    if (confirmDelete) {
        AlertDialog(
            onDismissRequest = { confirmDelete = false },
            title = { Text("Delete “${d.original?.name}”?") },
            text = { Text("It's removed from your PC and, on their next sync, from your devices. The songs stay.") },
            confirmButton = { TextButton(onClick = { confirmDelete = false; onDelete() }) { Text("Delete", color = Palette.Err) } },
            dismissButton = { TextButton(onClick = { confirmDelete = false }) { Text("Cancel") } },
        )
    }
}

@Composable
private fun AddSongs(all: List<Track>, already: Set<String>, onDismiss: () -> Unit, onAdd: (Track) -> Unit) {
    var query by remember { mutableStateOf("") }
    val added = remember { mutableStateListOf<String>() }
    val q = query.trim().lowercase()
    val shown = remember(q, all) {
        if (q.isEmpty()) all.sortedBy { it.albumArtist.lowercase() + it.album.lowercase() }.take(200)
        else all.filter { q in it.title.lowercase() || q in it.artist.lowercase() || q in it.album.lowercase() }.take(200)
    }
    AlertDialog(
        onDismissRequest = onDismiss,
        title = { Text("Add songs") },
        text = {
            Column(verticalArrangement = Arrangement.spacedBy(8.dp)) {
                OutlinedTextField(query, { query = it }, Modifier.fillMaxWidth(), singleLine = true,
                    leadingIcon = { Icon(Icons.Default.Search, null) }, placeholder = { Text("Title, artist or album") })
                if (all.isEmpty()) Hint("Sync once to load your library.")
                LazyColumn(Modifier.heightIn(max = 420.dp)) {
                    items(shown, key = { it.path }) { t ->
                        val inList = t.path in already || t.path in added
                        Row(Modifier.fillMaxWidth().clickable(enabled = !inList) { onAdd(t); added += t.path }
                            .padding(vertical = 8.dp), verticalAlignment = Alignment.CenterVertically) {
                            Column(Modifier.weight(1f)) {
                                Text(t.title, maxLines = 1, overflow = TextOverflow.Ellipsis,
                                    color = if (inList) Palette.Muted else MaterialTheme.colorScheme.onSurface)
                                Hint("${t.artist} · ${t.album}")
                            }
                            if (inList) Icon(Icons.Default.Check, null, tint = Palette.Ok)
                        }
                    }
                }
                if (q.isEmpty() && all.size > 200) Hint("Search to find more songs.")
            }
        },
        confirmButton = { TextButton(onClick = onDismiss) { Text("Done") } },
    )
}

@Composable
private fun NameDialog(current: String, existing: List<String>, onDismiss: () -> Unit, onDone: (String) -> Unit) {
    var name by remember { mutableStateOf(current) }
    val clean = cleanPlaylistName(name)
    val taken = existing.any { it.equals(clean, ignoreCase = true) }
    AlertDialog(
        onDismissRequest = onDismiss,
        title = { Text(if (current.isEmpty()) "New playlist" else "Rename playlist") },
        text = {
            Column(verticalArrangement = Arrangement.spacedBy(6.dp)) {
                OutlinedTextField(name, { name = it }, singleLine = true, label = { Text("Name") })
                if (taken) Hint("There's already a playlist with that name.", Palette.Err)
                else if (clean != name.trim() && clean.isNotEmpty()) Hint("Will be saved as “$clean”.")
            }
        },
        confirmButton = { TextButton(onClick = { onDone(clean) }, enabled = clean.isNotEmpty() && !taken) { Text("OK") } },
        dismissButton = { TextButton(onClick = onDismiss) { Text("Cancel") } },
    )
}
