package io.github.tianxibang.musictools.net

import kotlinx.serialization.SerialName
import kotlinx.serialization.Serializable
import java.security.MessageDigest
import javax.crypto.Mac
import javax.crypto.spec.SecretKeySpec

/** Mirrors musictools/core/netsync on the desktop side. Keep the two in step. */
object Protocol {
    const val VERSION = 1
    const val SERVICE_TYPE = "_musictools._tcp."
    const val DEFAULT_PORT = 47321

    /** Same normalisation as security.normalize_code: forgiving about case, dashes and look-alikes. */
    fun normalizeCode(code: String): String =
        code.uppercase().replace("-", "").replace(" ", "")
            .map { when (it) { 'O' -> '0'; 'I', 'L' -> '1'; else -> it } }
            .joinToString("")

    private fun proof(code: String, role: String, fingerprint: String, nonce: String, deviceId: String): String {
        val mac = Mac.getInstance("HmacSHA256")
        mac.init(SecretKeySpec(normalizeCode(code).toByteArray(), "HmacSHA256"))
        val msg = "mt-pair-v1|$role|${fingerprint.lowercase()}|$nonce|$deviceId"
        return mac.doFinal(msg.toByteArray()).toHex()
    }

    /** Sent by the phone: proves it knows the code, bound to the certificate it actually sees. */
    fun clientProof(code: String, fingerprint: String, nonce: String, deviceId: String) =
        proof(code, "client", fingerprint, nonce, deviceId)

    /** Expected from the PC: proves the PC knows the code too. */
    fun serverProof(code: String, fingerprint: String, nonce: String, deviceId: String) =
        proof(code, "server", fingerprint, nonce, deviceId)

    fun sha256(bytes: ByteArray): String = MessageDigest.getInstance("SHA-256").digest(bytes).toHex()

    /** Constant-time comparison for proofs and fingerprints. */
    fun same(a: String, b: String) = MessageDigest.isEqual(a.lowercase().toByteArray(), b.lowercase().toByteArray())
}

fun ByteArray.toHex(): String = joinToString("") { "%02x".format(it) }

/** What the QR code on the PC holds: musictools://pair?v=1&h=ip1,ip2&p=port&fp=…&c=code&id=… */
data class PairingInvite(val hosts: List<String>, val port: Int, val fingerprint: String, val code: String,
                         val serverId: String) {
    companion object {
        fun parse(text: String): PairingInvite? {
            if (!text.startsWith("musictools://pair?")) return null
            val q = text.substringAfter('?').split('&').mapNotNull {
                val i = it.indexOf('=')
                if (i > 0) it.substring(0, i) to java.net.URLDecoder.decode(it.substring(i + 1), "UTF-8") else null
            }.toMap()
            val fp = q["fp"]?.lowercase()?.takeIf { it.matches(Regex("[0-9a-f]{64}")) } ?: return null
            val hosts = q["h"]?.split(',')?.filter { it.isNotBlank() }?.takeIf { it.isNotEmpty() } ?: return null
            return PairingInvite(hosts, q["p"]?.toIntOrNull() ?: Protocol.DEFAULT_PORT, fp, q["c"] ?: return null,
                q["id"] ?: "")
        }
    }
}

@Serializable
data class ServerInfo(
    val app: String = "",
    val version: String = "",
    val protocol: Int = 0,
    val id: String = "",
    val name: String = "",
    val fingerprint: String = "",
    val pairing: String = "required",
)

@Serializable
data class PairRequest(
    @SerialName("device_id") val deviceId: String,
    val name: String,
    val kind: String = "phone",
    val nonce: String,
    val proof: String,
)

@Serializable
data class PairResponse(val token: String, val proof: String, val id: String = "", val name: String = "")

@Serializable
data class RemoteFile(val path: String, val size: Long, val mtime: Long = 0)

@Serializable
data class Manifest(val files: List<RemoteFile>, val generated: Long = 0)

@Serializable
data class InboxItem(
    val id: String,
    val name: String = "",
    val state: String,
    val detail: String = "",
    @SerialName("library_path") val libraryPath: String = "",
    @SerialName("opus_path") val opusPath: String = "",
) {
    val finished get() = state in setOf("ready", "duplicate", "failed")
}

@Serializable
data class InboxList(val items: List<InboxItem>)

@Serializable
data class ApiError(val error: String)

// --- phone tools (library, playlists, pipeline)

@Serializable
data class Track(
    val path: String,
    val size: Long = 0,
    val duration: Double = 0.0,
    val bitrate: Int = 0,
    val art: Boolean = false,
    /** Quality of the lossless original on the PC, e.g. "16-bit / 44.1 kHz" (empty on older PCs). */
    val source: String = "",
    val tags: Map<String, String> = emptyMap(),
) {
    val title get() = tags["title"] ?: path.substringAfterLast('/').substringBeforeLast('.')
    val artist get() = tags["artist"] ?: ""
    /** Grouping artist: album artist when set, so compilations stay together. */
    val albumArtist get() = tags["albumartist"]?.takeIf { it.isNotBlank() } ?: tags["artist"]?.takeIf { it.isNotBlank() }
        ?: "Unknown artist"
    val album get() = tags["album"]?.takeIf { it.isNotBlank() } ?: "Unknown album"
    /** Genre tags often list several ("Pop, Rock, Metal"): each counts on its own. */
    val genres get() = tags["genre"].orEmpty().split(',', ';', '/').map { it.trim() }.filter { it.isNotEmpty() }
    val year get() = tags["date"]?.take(4)?.toIntOrNull()
    val trackNo get() = tags["tracknumber"]?.substringBefore('/')?.trim()?.toIntOrNull() ?: 0
}

@Serializable
data class LibrarySummary(
    @SerialName("flac_count") val flacCount: Int = 0,
    @SerialName("flac_bytes") val flacBytes: Long = 0,
    @SerialName("opus_count") val opusCount: Int = 0,
    @SerialName("opus_bytes") val opusBytes: Long = 0,
    val pending: Int = 0,
)

@Serializable
data class LibraryResponse(val tracks: List<Track>, val summary: LibrarySummary = LibrarySummary())

@Serializable
data class Playlist(val name: String, val tracks: List<String>, val missing: Int = 0)

@Serializable
data class PlaylistList(val playlists: List<Playlist>)

@Serializable
data class SavePlaylist(val tracks: List<String>)

@Serializable
data class PipelineStep(val key: String, val label: String, val default: Boolean = false)

@Serializable
data class PipelineLine(val text: String, val level: String = "info")

@Serializable
data class PipelineProgress(val done: Int = 0, val total: Int = 0, val text: String = "")

@Serializable
data class PipelineStatus(
    val steps: List<PipelineStep> = emptyList(),
    val running: Boolean = false,
    /** A job started on the PC itself is running. */
    val busy: Boolean = false,
    @SerialName("started_by") val startedBy: String = "",
    val started: Double = 0.0,
    val finished: Double = 0.0,
    val error: String = "",
    val lines: List<PipelineLine> = emptyList(),
    val next: Int = 0,
    val progress: PipelineProgress = PipelineProgress(),
)

@Serializable
data class StartPipeline(val steps: List<String>)

/** Same rules as the desktop's safe_filename, so a name typed on the phone is accepted by the PC. */
fun cleanPlaylistName(name: String): String =
    name.replace(Regex("[?*<>\"]"), "").replace(":", " -").replace(Regex("[|/\\\\]"), "-")
        .replace(Regex("[\\x00-\\x1f]"), "").replace(Regex("\\s+"), " ").trim().trimEnd('.', ' ').take(180)
