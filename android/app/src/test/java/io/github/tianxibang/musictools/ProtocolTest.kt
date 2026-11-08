package io.github.tianxibang.musictools

import io.github.tianxibang.musictools.net.PairingInvite
import io.github.tianxibang.musictools.net.Protocol
import io.github.tianxibang.musictools.sync.SyncEngine
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNotNull
import org.junit.Assert.assertNull
import org.junit.Test
import java.io.File
import java.nio.file.Files

/** Expected values come from the desktop (musictools/core/netsync/security.py), so both sides agree. */
class ProtocolTest {
    private val fp = "ab".repeat(32)
    private val nonce = "cd".repeat(16)
    private val dev = "ef".repeat(16)

    @Test fun codeNormalisationMatchesDesktop() {
        assertEquals("7K3M9QX2ABCD", Protocol.normalizeCode("7k3m-9qx2-abcd"))
        assertEquals("0111", Protocol.normalizeCode("o1l-i"))
    }

    @Test fun proofsMatchDesktop() {
        assertEquals("6c348d21bc0c055f6c68d9b65f0c43107ca7a1918e335ca815a6a43936772d54",
            Protocol.clientProof("7K3M-9QX2-ABCD", fp, nonce, dev))
        assertEquals("a3a1c92d8a9668e75a93af046ad6ce1ac6c13a083015f2a890f5095fbd2edd37",
            Protocol.serverProof("7k3m 9qx2 abcd", fp, nonce, dev))
    }

    @Test fun proofDependsOnCertificate() {
        val other = "00".repeat(32)
        assert(Protocol.clientProof("7K3M9QX2ABCD", fp, nonce, dev) != Protocol.clientProof("7K3M9QX2ABCD", other, nonce, dev))
    }

    @Test fun parsesQrInvite() {
        val inv = PairingInvite.parse("musictools://pair?v=1&h=192.168.1.5,10.0.0.2&p=47321&fp=$fp&c=7K3M9QX2ABCD&id=abc")
        assertNotNull(inv)
        assertEquals(listOf("192.168.1.5", "10.0.0.2"), inv!!.hosts)
        assertEquals(47321, inv.port)
        assertEquals(fp, inv.fingerprint)
        assertNull(PairingInvite.parse("https://example.com/?fp=$fp"))
        assertNull(PairingInvite.parse("musictools://pair?h=1.2.3.4&fp=short&c=X"))
    }

    @Test fun serverPathsStayInsideMusicFolder() {
        val root = Files.createTempDirectory("music").toFile()
        assertEquals(File(root, "A/b.opus").canonicalFile, SyncEngine.safeChild(root, "A/b.opus"))
        for (bad in listOf("../x.opus", "A/../../x.opus", "/etc/passwd", "", "A\\..\\x", "..")) {
            assertNull(bad, SyncEngine.safeChild(root, bad))
        }
    }
}
