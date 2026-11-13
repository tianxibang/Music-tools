package io.github.tianxibang.musictools

import io.github.tianxibang.musictools.update.Updater
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test

class UpdaterTest {
    /** The same rules as the desktop's update.newer. */
    @Test fun versions() {
        assertTrue(Updater.newer("v4.2", "4.1.1"))
        assertTrue(Updater.newer("v4.1.2", "4.1.1"))
        assertTrue(Updater.newer("v10.0", "9.9"))
        assertFalse(Updater.newer("v4.1.1", "4.1.1"))
        assertFalse(Updater.newer("v4.2.0", "4.2"))
        assertFalse(Updater.newer("v4.1", "4.1.1"))
        assertFalse(Updater.newer("nightly", "4.1"))
    }

    @Test fun releaseFromGitHub() {
        val json = """{"tag_name": "v4.2", "name": "Music Tools 4.2: Foundation", "body": "notes",
            "assets": [{"name": "MusicTools-linux-x64.rpm", "browser_download_url": "https://x/rpm", "digest": null},
                       {"name": "MusicTools-android.apk", "browser_download_url": "https://x/apk",
                        "digest": "sha256:abc"}]}"""
        val rel = Updater.parse(json)!!
        assertEquals("4.2", rel.version)
        assertEquals("https://x/apk", rel.apkUrl)
        assertEquals("sha256:abc", rel.digest)
        assertNull(Updater.parse("""{"tag_name": "v4.2", "assets": []}"""))
    }

    @Test fun notesForTheDialog() {
        val md = "# Foundation\n*Music Tools 4.2 · released 2026-12-01*\n\n### Updates\n- **Update** from " +
            "[GitHub](https://github.com) with `one tap`\n\n---\n\n### 📦 Downloads\n| table |"
        assertEquals("Updates\n• Update from GitHub with one tap", Updater.plainNotes(md))
    }
}
