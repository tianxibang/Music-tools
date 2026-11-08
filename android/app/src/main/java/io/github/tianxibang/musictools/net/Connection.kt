package io.github.tianxibang.musictools.net

import android.content.Context
import io.github.tianxibang.musictools.data.Prefs
import io.github.tianxibang.musictools.sync.SyncEngine
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.currentCoroutineContext
import kotlinx.coroutines.ensureActive
import kotlinx.coroutines.flow.first
import kotlinx.coroutines.withContext
import kotlinx.coroutines.withTimeoutOrNull
import java.io.IOException

/** Finding the paired PC: shared by the sync and by the screens that talk to the PC directly. */
object Connection {
    @Volatile private var last: SyncClient? = null

    /**
     * USB cable first (the PC forwards our localhost with `adb reverse`; refused instantly without a
     * cable), then the last known addresses, then whatever the network announces for this PC's id.
     */
    suspend fun open(context: Context): SyncClient = withContext(Dispatchers.IO) {
        val prefs = Prefs.get(context)
        val pc = prefs.pc ?: throw SyncException(0, "Not paired with a PC yet")
        fun client(host: String, port: Int) = SyncClient(host, port, prefs.deviceId, pc.fingerprint, pc.token)
        // Anything else on the phone could listen on localhost, but it can't show the PC's pinned certificate
        runCatching { client(SyncEngine.USB_HOST, pc.port).also { it.info() } }.getOrNull()?.let { return@withContext remember(it) }
        for (host in pc.hosts) {
            currentCoroutineContext().ensureActive()
            val c = client(host, pc.port)
            try {
                c.info()
                return@withContext remember(c)
            } catch (e: SyncException) {
                throw e  // reachable but refused (e.g. certificate changed): don't try elsewhere
            } catch (_: IOException) {
            }
        }
        val found = withTimeoutOrNull(6000) {
            discoverPcs(context).first { list -> list.any { it.id == pc.id } }.first { it.id == pc.id }
        } ?: throw IOException("Couldn't reach ${pc.name}. Is Music Tools running with sync turned on, and is " +
            "the phone on the same network${if (prefs.settings.wifiOnly) " (Wi-Fi)" else ""} or plugged in by USB?")
        val c = client(found.host, found.port)
        c.info()
        prefs.rememberHost(found.host, found.port)
        remember(c)
    }

    private fun remember(c: SyncClient) = c.also { last = it }

    /**
     * Run [block] with a client, reusing the last working connection when it still works. Screens use
     * this for quick calls; a PC on an older Music Tools gets a clear message instead of "HTTP 404".
     */
    suspend fun <T> use(context: Context, block: (SyncClient) -> T): T = withContext(Dispatchers.IO) {
        val pc = Prefs.get(context).pc
        val cached = last?.takeIf { pc != null && it.pinnedFingerprint == pc.fingerprint && it.token == pc.token }
        try {
            if (cached != null) {
                try {
                    return@withContext block(cached)
                } catch (e: SyncException) {
                    throw e  // the PC answered; reconnecting won't change its answer
                } catch (_: IOException) {
                    last = null  // gone (e.g. cable unplugged): find the PC again
                }
            }
            block(open(context))
        } catch (e: SyncException) {
            if (e.status == 404 && e.message == "Not found") {
                throw SyncException(404, "Update Music Tools on your PC to use this.")
            }
            throw e
        }
    }
}
