package io.github.tianxibang.musictools.net

import android.content.Context
import android.net.nsd.NsdManager
import android.net.nsd.NsdServiceInfo
import android.net.wifi.WifiManager
import android.os.Build
import kotlinx.coroutines.channels.awaitClose
import kotlinx.coroutines.flow.Flow
import io.github.tianxibang.musictools.sync.SyncEngine
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.delay
import kotlinx.coroutines.flow.callbackFlow
import kotlinx.coroutines.flow.flow
import kotlinx.coroutines.withContext
import java.util.concurrent.Executors

/** A Music Tools PC announced on the local network, or reachable over a USB cable. */
data class FoundPc(val name: String, val id: String, val host: String, val port: Int, val shortFingerprint: String,
                   val open: Boolean, val usb: Boolean = false)

/** The PC behind a USB cable (forwarded with adb reverse), checked every few seconds; null when there's none. */
fun usbPc(): Flow<FoundPc?> = flow {
    while (true) {
        val pc = withContext(Dispatchers.IO) {
            runCatching {
                val c = SyncClient(SyncEngine.USB_HOST, Protocol.DEFAULT_PORT, "0".repeat(32), null, null)
                val info = c.info()
                FoundPc(info.name, info.id, SyncEngine.USB_HOST, Protocol.DEFAULT_PORT,
                    c.seenFingerprint.orEmpty().take(16), info.pairing == "open", usb = true)
            }.getOrNull()
        }
        emit(pc)
        delay(3000)
    }
}

/** PCs running the sync server, found via mDNS. Emits the full list whenever it changes. */
fun discoverPcs(context: Context): Flow<List<FoundPc>> = callbackFlow {
    val nsd = context.getSystemService(Context.NSD_SERVICE) as NsdManager
    val wifi = context.applicationContext.getSystemService(Context.WIFI_SERVICE) as WifiManager
    val lock = wifi.createMulticastLock("musictools-discovery").apply { setReferenceCounted(false); acquire() }
    val found = LinkedHashMap<String, FoundPc>()
    val executor = Executors.newSingleThreadExecutor()

    fun publish() = trySend(synchronized(found) { found.values.toList() })

    fun resolved(info: NsdServiceInfo) {
        val attrs = info.attributes.mapValues { (_, v) -> v?.toString(Charsets.UTF_8).orEmpty() }
        // A PC can announce several addresses; 169.254.x.x ones (adapters with no real network) don't reach it
        val host = if (Build.VERSION.SDK_INT >= 34) info.hostAddresses.sortedBy { it.isLinkLocalAddress }.firstOrNull()?.hostAddress
        else @Suppress("DEPRECATION") info.host?.hostAddress
        if (host == null || attrs["v"] != Protocol.VERSION.toString()) return
        val pc = FoundPc(attrs["name"] ?: info.serviceName, attrs["id"].orEmpty(), host, info.port,
            attrs["fp"].orEmpty(), attrs["pairing"] == "open")
        synchronized(found) { found[info.serviceName] = pc }
        publish()
    }

    @Suppress("DEPRECATION")
    fun resolve(info: NsdServiceInfo) {
        if (Build.VERSION.SDK_INT >= 34) {
            nsd.registerServiceInfoCallback(info, executor, object : NsdManager.ServiceInfoCallback {
                override fun onServiceInfoCallbackRegistrationFailed(errorCode: Int) {}
                override fun onServiceUpdated(serviceInfo: NsdServiceInfo) {
                    resolved(serviceInfo)
                    runCatching { nsd.unregisterServiceInfoCallback(this) }
                }
                override fun onServiceLost() {}
                override fun onServiceInfoCallbackUnregistered() {}
            })
        } else {
            nsd.resolveService(info, object : NsdManager.ResolveListener {
                override fun onResolveFailed(serviceInfo: NsdServiceInfo, errorCode: Int) {}
                override fun onServiceResolved(serviceInfo: NsdServiceInfo) = resolved(serviceInfo)
            })
        }
    }

    val listener = object : NsdManager.DiscoveryListener {
        override fun onStartDiscoveryFailed(serviceType: String, errorCode: Int) { close() }
        override fun onStopDiscoveryFailed(serviceType: String, errorCode: Int) {}
        override fun onDiscoveryStarted(serviceType: String) { publish() }
        override fun onDiscoveryStopped(serviceType: String) {}
        override fun onServiceFound(info: NsdServiceInfo) { executor.execute { resolve(info) } }
        override fun onServiceLost(info: NsdServiceInfo) {
            synchronized(found) { found.remove(info.serviceName) }
            publish()
        }
    }
    nsd.discoverServices(Protocol.SERVICE_TYPE, NsdManager.PROTOCOL_DNS_SD, listener)
    awaitClose {
        runCatching { nsd.stopServiceDiscovery(listener) }
        executor.shutdown()
        lock.release()
    }
}
