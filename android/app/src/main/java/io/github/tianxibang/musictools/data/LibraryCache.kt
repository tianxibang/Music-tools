package io.github.tianxibang.musictools.data

import android.content.Context
import io.github.tianxibang.musictools.net.LibraryResponse
import io.github.tianxibang.musictools.net.Playlist
import io.github.tianxibang.musictools.net.Track
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.serialization.Serializable
import kotlinx.serialization.json.Json
import java.io.File

@Serializable
data class LibrarySnapshot(
    val library: LibraryResponse = LibraryResponse(emptyList()),
    val playlists: List<Playlist> = emptyList(),
    val updated: Long = 0,
) {
    val byPath: Map<String, Track> by lazy { library.tracks.associateBy { it.path } }
}

/** The PC's track list and playlists as last seen, so stats and choosing what to sync work offline. */
class LibraryCache private constructor(context: Context) {
    private val file = File(context.filesDir, "library.json")
    private val json = Json { ignoreUnknownKeys = true }
    private val _state = MutableStateFlow(
        runCatching { json.decodeFromString<LibrarySnapshot>(file.readText()) }.getOrDefault(LibrarySnapshot())
    )
    val state: StateFlow<LibrarySnapshot> = _state.asStateFlow()

    fun update(library: LibraryResponse? = null, playlists: List<Playlist>? = null) {
        val old = _state.value
        val snap = LibrarySnapshot(library ?: old.library, playlists ?: old.playlists, System.currentTimeMillis())
        val tmp = File(file.parentFile, file.name + ".tmp")
        tmp.writeText(json.encodeToString(LibrarySnapshot.serializer(), snap))
        tmp.renameTo(file)
        _state.value = snap
    }

    companion object {
        @Volatile private var instance: LibraryCache? = null
        fun get(context: Context) = instance ?: synchronized(this) {
            instance ?: LibraryCache(context.applicationContext).also { instance = it }
        }
    }
}

/**
 * What goes on the phone when not syncing everything. Keys are stored in the settings:
 * "artist:<album artist>", "album:<album artist>\n<album>" and "playlist:<name>".
 */
object Selection {
    fun artist(name: String) = "artist:$name"
    fun album(artist: String, album: String) = "album:$artist\n$album"
    fun playlist(name: String) = "playlist:$name"

    /** Export paths to keep on the phone. Playlist files are included only for selected playlists. */
    fun wantedPaths(snap: LibrarySnapshot, selection: Set<String>, sentFromPhone: Set<String>): Set<String> {
        val out = HashSet<String>(sentFromPhone)
        for (t in snap.library.tracks) {
            if (artist(t.albumArtist) in selection || album(t.albumArtist, t.album) in selection) out += t.path
        }
        for (pl in snap.playlists) {
            if (playlist(pl.name) in selection) {
                out += pl.tracks
                out += "Playlists/${pl.name}.m3u8"
            }
        }
        return out
    }
}
