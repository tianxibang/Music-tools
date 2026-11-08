package io.github.tianxibang.musictools

import io.github.tianxibang.musictools.net.SyncClient
import io.github.tianxibang.musictools.net.SyncException
import io.github.tianxibang.musictools.sync.SyncEngine
import org.junit.Assert.assertArrayEquals
import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Assume.assumeTrue
import org.junit.Test
import java.io.File
import java.nio.file.Files

/**
 * Talks to a real desktop server. Run via tests/android_interop.sh, which starts one and passes:
 * MT_HOST, MT_PORT, MT_CODE (pairing code) and MT_FLAC (a FLAC file to send).
 */
class ServerInteropTest {
    @Test fun fullRoundTrip() {
        val host = System.getenv("MT_HOST")
        assumeTrue("no test server", host != null)
        val port = System.getenv("MT_PORT")!!.toInt()
        val device = "12".repeat(16)

        // Typed code (no pinned fingerprint): the proof exchange must still succeed
        val (paired, fp) = SyncClient(host!!, port, device, null, null).pair(System.getenv("MT_CODE")!!, "Interop test")
        val client = SyncClient(host, port, device, fp, paired.token)

        // A wrong pinned fingerprint is refused before anything is sent
        val wrong = SyncClient(host, port, device, "00".repeat(32), paired.token)
        val err = runCatching { wrong.manifest() }.exceptionOrNull()
        assertTrue("$err", err is SyncException && err.message!!.contains("certificate changed"))

        val flac = File(System.getenv("MT_FLAC")!!)
        var item = client.upload(flac, SyncEngine.sha256(flac))
        val deadline = System.currentTimeMillis() + 60_000
        while (!item.finished && System.currentTimeMillis() < deadline) {
            Thread.sleep(300)
            item = client.inboxItem(item.id)
        }
        assertEquals("ready", item.state)

        val remote = client.manifest().files.first { it.path == item.opusPath }
        val dest = File(Files.createTempDirectory("phone").toFile(), item.opusPath)
        client.download(item.opusPath, dest, remote.size)
        assertEquals(remote.size, dest.length())
        assertArrayEquals("OggS".toByteArray(), dest.readBytes().copyOf(4))

        client.acknowledge(item.id)
        assertEquals(0, client.inbox().size)
        client.unpair()
        assertTrue(runCatching { client.manifest() }.exceptionOrNull().let { it is SyncException && it.status == 401 })
    }
}
