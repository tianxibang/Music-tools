package io.github.tianxibang.musictools.net

import io.github.tianxibang.musictools.data.PairedPc
import io.github.tianxibang.musictools.data.Prefs
import io.github.tianxibang.musictools.sync.SyncEngine
import java.io.IOException

object Pairing {
    /**
     * Pair using a code. [fingerprint] comes from the QR code when scanned; with a typed code it's
     * null and the HMAC proof is what rules out a man in the middle.
     */
    fun withCode(prefs: Prefs, hosts: List<String>, port: Int, code: String, fingerprint: String?,
                 shortFingerprint: String? = null): PairedPc {
        var last: Exception = IOException("No address to try")
        for (host in hosts) {
            val client = SyncClient(host, port, prefs.deviceId, fingerprint, null)
            try {
                val (res, fp) = client.pair(code, prefs.state.value.deviceName)
                if (shortFingerprint != null && shortFingerprint.isNotEmpty() && !fp.startsWith(shortFingerprint)) {
                    throw SyncException(0, "This PC doesn't match the one announced on the network")
                }
                // The USB address is always tried anyway, so only network addresses are remembered
                val known = (listOf(host) + hosts.filter { it != host }).filter { it != SyncEngine.USB_HOST }
                val pc = PairedPc(res.id, res.name, known, port, fp, res.token)
                prefs.setPc(pc)
                return pc
            } catch (e: SyncException) {
                throw e  // the PC answered: wrong code, expired, … no point trying other addresses
            } catch (e: IOException) {
                last = e
            }
        }
        throw IOException("Couldn't reach the PC (${last.message})")
    }

    /**
     * Connect to a PC in open mode (no pairing). Its certificate is remembered on first contact,
     * so later syncs still notice if something else answers at that address.
     */
    fun open(prefs: Prefs, host: String, port: Int): PairedPc {
        val client = SyncClient(host, port, prefs.deviceId, null, null)
        val info = client.info()
        if (info.pairing != "open") throw SyncException(403, "This PC requires pairing. Ask it for a code.")
        val fp = client.seenFingerprint ?: throw SyncException(0, "No certificate from the PC")
        if (!Protocol.same(fp, info.fingerprint)) throw SyncException(0, "Certificate mismatch")
        client.manifest()  // confirms access really works
        val pc = PairedPc(info.id, info.name, listOf(host).filter { it != SyncEngine.USB_HOST }, port, fp, null)
        prefs.setPc(pc)
        return pc
    }

    /** Tell the PC to forget us (best effort), then forget it. */
    fun unpair(prefs: Prefs) {
        val pc = prefs.pc ?: return
        if (pc.token != null) {
            for (host in pc.hosts) {
                if (runCatching { SyncClient(host, pc.port, prefs.deviceId, pc.fingerprint, pc.token).unpair() }
                        .isSuccess) break
            }
        }
        prefs.setPc(null)
    }
}
