package io.github.tianxibang.musictools.data

import android.content.Context
import android.content.SharedPreferences
import android.os.Environment
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import java.io.File
import java.security.SecureRandom

/** The PC this phone syncs with. [token] is null for a PC in open mode (no pairing). */
data class PairedPc(
    val id: String,
    val name: String,
    val hosts: List<String>,
    val port: Int,
    val fingerprint: String,
    val token: String?,
)

data class Settings(
    /** Where the PC's Opus export is mirrored. Same default as the desktop's adb sync, so files carry over. */
    val musicDir: String,
    /** Folders searched for new FLACs to send to the PC. */
    val sourceDirs: List<String>,
    val deleteFlacAfterSync: Boolean = true,
    val keepExtraFiles: Boolean = false,
    val autoSync: Boolean = true,
    val wifiOnly: Boolean = true,
    /** false: only [selection] goes on the phone (see [Selection]). */
    val syncAll: Boolean = true,
    val selection: Set<String> = emptySet(),
)

data class PrefsState(val pc: PairedPc?, val settings: Settings, val deviceId: String, val deviceName: String)

/**
 * App settings and the pairing, in app-private storage (other apps can't read it, and backups
 * are disabled in the manifest so the token stays on this phone).
 */
class Prefs private constructor(context: Context) {
    private val sp: SharedPreferences = context.getSharedPreferences("musictools", Context.MODE_PRIVATE)
    private val _state = MutableStateFlow(load())
    val state: StateFlow<PrefsState> = _state.asStateFlow()

    val pc get() = _state.value.pc
    val settings get() = _state.value.settings
    val deviceId get() = _state.value.deviceId

    private fun load(): PrefsState {
        var id = sp.getString("device_id", null)
        if (id == null) {
            id = ByteArray(16).also { SecureRandom().nextBytes(it) }.joinToString("") { "%02x".format(it) }
            sp.edit().putString("device_id", id).apply()
        }
        val root = Environment.getExternalStorageDirectory().absolutePath
        val pc = sp.getString("pc_id", null)?.let {
            PairedPc(it, sp.getString("pc_name", "PC")!!, sp.getString("pc_hosts", "")!!.split(',').filter(String::isNotBlank),
                sp.getInt("pc_port", 47321), sp.getString("pc_fp", "")!!, sp.getString("pc_token", null))
        }
        val settings = Settings(
            musicDir = sp.getString("music_dir", null) ?: "$root/Music/opus_export",
            sourceDirs = sp.getString("source_dirs", null)?.split('\n')?.filter(String::isNotBlank)
                ?: listOf("$root/Download", "$root/Music"),
            deleteFlacAfterSync = sp.getBoolean("delete_flac", true),
            keepExtraFiles = sp.getBoolean("keep_extra", false),
            autoSync = sp.getBoolean("auto_sync", true),
            wifiOnly = sp.getBoolean("wifi_only", true),
            syncAll = sp.getBoolean("sync_all", true),
            selection = sp.getStringSet("selection", emptySet())!!.toSet(),
        )
        val name = sp.getString("device_name", null) ?: "${android.os.Build.MANUFACTURER} ${android.os.Build.MODEL}"
            .replaceFirstChar { it.uppercase() }
        return PrefsState(pc, settings, id, name)
    }

    fun setPc(pc: PairedPc?) {
        sp.edit().apply {
            if (pc == null) {
                listOf("pc_id", "pc_name", "pc_hosts", "pc_port", "pc_fp", "pc_token").forEach(::remove)
            } else {
                putString("pc_id", pc.id).putString("pc_name", pc.name).putString("pc_hosts", pc.hosts.joinToString(","))
                putInt("pc_port", pc.port).putString("pc_fp", pc.fingerprint)
                if (pc.token != null) putString("pc_token", pc.token) else remove("pc_token")
            }
        }.commit()
        _state.value = load()
    }

    /** Remember an address the PC was last reachable at, first in the list. */
    fun rememberHost(host: String, port: Int) {
        val pc = pc ?: return
        if (pc.hosts.firstOrNull() == host && pc.port == port) return
        setPc(pc.copy(hosts = (listOf(host) + pc.hosts.filter { it != host }).take(5), port = port))
    }

    fun update(block: (Settings) -> Settings) {
        val s = block(settings)
        sp.edit().putString("music_dir", s.musicDir).putString("source_dirs", s.sourceDirs.joinToString("\n"))
            .putBoolean("delete_flac", s.deleteFlacAfterSync).putBoolean("keep_extra", s.keepExtraFiles)
            .putBoolean("auto_sync", s.autoSync).putBoolean("wifi_only", s.wifiOnly)
            .putBoolean("sync_all", s.syncAll).putStringSet("selection", s.selection).commit()
        _state.value = load()
    }

    fun setDeviceName(name: String) {
        sp.edit().putString("device_name", name.trim().take(80)).commit()
        _state.value = load()
    }

    /** Opus files that came back for FLACs sent from this phone: always kept, whatever is selected. */
    val sentFromPhone: Set<String> get() = sp.getStringSet("sent_from_phone", emptySet())!!.toSet()

    fun addSentFromPhone(path: String) {
        sp.edit().putStringSet("sent_from_phone", sentFromPhone + path).apply()
    }

    /** Uploads that reached the PC but whose round trip isn't finished, keyed by FLAC path. */
    fun pendingFile(context: Context) = File(context.filesDir, "pending.json")

    companion object {
        @Volatile private var instance: Prefs? = null
        fun get(context: Context) = instance ?: synchronized(this) {
            instance ?: Prefs(context.applicationContext).also { instance = it }
        }
    }
}
