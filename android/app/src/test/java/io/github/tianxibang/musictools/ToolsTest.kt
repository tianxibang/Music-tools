package io.github.tianxibang.musictools

import io.github.tianxibang.musictools.data.LibrarySnapshot
import io.github.tianxibang.musictools.data.Selection
import io.github.tianxibang.musictools.net.LibraryResponse
import io.github.tianxibang.musictools.net.Playlist
import io.github.tianxibang.musictools.net.Track
import io.github.tianxibang.musictools.net.cleanPlaylistName
import org.junit.Assert.assertEquals
import org.junit.Test

class ToolsTest {
    /** Expected values from the desktop's safe_filename, which the PC uses to accept a playlist name. */
    @Test fun playlistNamesCleanedLikeDesktop() {
        assertEquals("Road trip", cleanPlaylistName("Road trip"))
        assertEquals("AC-DC - Best", cleanPlaylistName("AC/DC: Best?"))
        assertEquals("abc-de", cleanPlaylistName("a<b>c|d\"e*"))
        assertEquals("spaced out", cleanPlaylistName("  spaced   out  "))
        assertEquals("Trailing dots", cleanPlaylistName("Trailing dots..."))
        assertEquals("back-slash", cleanPlaylistName("back\\slash"))
    }

    private fun track(path: String, artist: String, album: String, albumArtist: String? = null) =
        Track(path, tags = buildMap {
            put("artist", artist); put("album", album)
            if (albumArtist != null) put("albumartist", albumArtist)
        })

    private val snap = LibrarySnapshot(
        LibraryResponse(listOf(
            track("A/One/1.opus", "A", "One"),
            track("A/Two/1.opus", "A", "Two"),
            track("B/X/1.opus", "B", "X"),
            track("Comp/Hits/1.opus", "Guest", "Hits", albumArtist = "Various"),
        )),
        listOf(Playlist("Mix", listOf("B/X/1.opus"))),
    )

    @Test fun artistCoversAllAlbums() {
        assertEquals(setOf("A/One/1.opus", "A/Two/1.opus"),
            Selection.wantedPaths(snap, setOf(Selection.artist("A")), emptySet()))
    }

    @Test fun singleAlbumAndCompilationsByAlbumArtist() {
        assertEquals(setOf("A/Two/1.opus", "Comp/Hits/1.opus"),
            Selection.wantedPaths(snap, setOf(Selection.album("A", "Two"), Selection.artist("Various")), emptySet()))
    }

    @Test fun playlistBringsItsSongsAndFile() {
        assertEquals(setOf("B/X/1.opus", "Playlists/Mix.m3u8"),
            Selection.wantedPaths(snap, setOf(Selection.playlist("Mix")), emptySet()))
    }

    @Test fun songsSentFromPhoneAlwaysStay() {
        assertEquals(setOf("Phone/Find.opus"), Selection.wantedPaths(snap, emptySet(), setOf("Phone/Find.opus")))
    }
}
