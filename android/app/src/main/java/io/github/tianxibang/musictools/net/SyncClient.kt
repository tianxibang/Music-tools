package io.github.tianxibang.musictools.net

import kotlinx.serialization.json.Json
import okhttp3.HttpUrl
import okhttp3.MediaType.Companion.toMediaType
import okhttp3.OkHttpClient
import okhttp3.Request
import okhttp3.RequestBody
import okhttp3.RequestBody.Companion.toRequestBody
import okhttp3.Response
import okio.BufferedSink
import okio.source
import java.io.File
import java.io.IOException
import java.security.MessageDigest
import java.security.SecureRandom
import java.security.cert.CertificateException
import java.security.cert.X509Certificate
import java.util.concurrent.TimeUnit
import javax.net.ssl.SSLContext
import javax.net.ssl.X509TrustManager

class SyncException(val status: Int, message: String) : IOException(message)

/**
 * HTTPS client for the desktop sync server.
 *
 * The PC uses a self-signed certificate, so instead of trusting a CA we pin the SHA-256 of its
 * certificate: learned from the QR code, or verified through the pairing proof when the code was
 * typed. A connection to anything presenting another certificate fails before any data is sent.
 */
class SyncClient(
    val host: String,
    val port: Int,
    private val deviceId: String,
    val pinnedFingerprint: String?,
    val token: String?,
) {
    /** Fingerprint of the certificate seen on the last connection (used while pairing). */
    @Volatile var seenFingerprint: String? = null
        private set

    private val json = Json { ignoreUnknownKeys = true }

    private val trustManager = object : X509TrustManager {
        override fun checkClientTrusted(chain: Array<X509Certificate>, authType: String) =
            throw CertificateException("not a server")

        override fun checkServerTrusted(chain: Array<X509Certificate>, authType: String) {
            val leaf = chain.firstOrNull() ?: throw CertificateException("no certificate")
            val fp = MessageDigest.getInstance("SHA-256").digest(leaf.encoded).toHex()
            seenFingerprint = fp
            if (pinnedFingerprint != null && !Protocol.same(fp, pinnedFingerprint)) {
                throw CertificateException("certificate mismatch")
            }
        }

        override fun getAcceptedIssuers(): Array<X509Certificate> = emptyArray()
    }

    private val http: OkHttpClient = run {
        val ssl = SSLContext.getInstance("TLS").apply { init(null, arrayOf(trustManager), SecureRandom()) }
        OkHttpClient.Builder()
            .sslSocketFactory(ssl.socketFactory, trustManager)
            // The certificate itself is pinned above, so the host name (an IP address) doesn't need checking
            .hostnameVerifier { _, _ -> true }
            .connectTimeout(5, TimeUnit.SECONDS)
            .readTimeout(120, TimeUnit.SECONDS)
            .writeTimeout(120, TimeUnit.SECONDS)
            .build()
    }

    private fun url(path: String, query: Map<String, String> = emptyMap()): HttpUrl {
        val b = HttpUrl.Builder().scheme("https").host(host).port(port).addPathSegments("api/v1")
        path.split('/').filter { it.isNotEmpty() }.forEach { b.addPathSegment(it) }
        query.forEach { (k, v) -> b.addQueryParameter(k, v) }
        return b.build()
    }

    private fun request(url: HttpUrl): Request.Builder {
        val b = Request.Builder().url(url).header("X-Device-Id", deviceId)
        if (token != null) b.header("Authorization", "Bearer $token")
        return b
    }

    private fun execute(req: Request): Response {
        val resp = try {
            http.newCall(req).execute()
        } catch (e: javax.net.ssl.SSLHandshakeException) {
            if (e.cause is CertificateException || e.message?.contains("mismatch") == true) {
                throw SyncException(0, "The PC's security certificate changed. Someone may be intercepting " +
                    "the connection, or Music Tools was reinstalled on the PC: pair again if you trust it.")
            }
            throw e
        }
        if (!resp.isSuccessful) {
            val body = resp.body?.string().orEmpty()
            resp.close()
            val msg = runCatching { json.decodeFromString<ApiError>(body).error }.getOrElse { "HTTP ${resp.code}" }
            throw SyncException(resp.code, msg)
        }
        return resp
    }

    private inline fun <reified T> call(req: Request): T =
        execute(req).use { json.decodeFromString(it.body!!.string()) }

    private val jsonType = "application/json".toMediaType()

    fun info(): ServerInfo = call(request(url("info")).get().build())

    /**
     * Pair with a code shown on the PC. The proof binds the code to the certificate we see, so a
     * man in the middle (who must show its own certificate) can't relay it to the real PC.
     */
    fun pair(code: String, name: String): Pair<PairResponse, String> {
        info()  // connect once to learn the certificate
        val fp = seenFingerprint ?: throw SyncException(0, "No certificate from the PC")
        val nonce = ByteArray(16).also { SecureRandom().nextBytes(it) }.toHex()
        val body = json.encodeToString(PairRequest.serializer(),
            PairRequest(deviceId, name, "phone", nonce, Protocol.clientProof(code, fp, nonce, deviceId)))
        val res: PairResponse = call(request(url("pair")).post(body.toRequestBody(jsonType)).build())
        if (seenFingerprint != fp) throw SyncException(0, "The PC's certificate changed during pairing")
        if (!Protocol.same(res.proof, Protocol.serverProof(code, fp, nonce, deviceId))) {
            throw SyncException(0, "The PC couldn't prove it knows the code. Not pairing.")
        }
        return res to fp
    }

    fun unpair() {
        execute(request(url("unpair")).post(ByteArray(0).toRequestBody(jsonType)).build()).close()
    }

    fun manifest(): Manifest = call(request(url("manifest")).get().build())

    /** Download an export file into [dest] via a temp file, so a cut connection never leaves half a song. */
    fun download(path: String, dest: File, expectedSize: Long, progress: (Long) -> Unit = {}) {
        val tmp = File(dest.parentFile, ".${dest.name}.part")
        dest.parentFile?.mkdirs()
        execute(request(url("files/$path")).get().build()).use { resp ->
            resp.body!!.byteStream().use { input ->
                tmp.outputStream().use { out ->
                    val buf = ByteArray(1 shl 16)
                    var total = 0L
                    while (true) {
                        val n = input.read(buf)
                        if (n < 0) break
                        out.write(buf, 0, n)
                        total += n
                        progress(total)
                    }
                }
            }
        }
        if (expectedSize >= 0 && tmp.length() != expectedSize) {
            tmp.delete()
            throw SyncException(0, "Download of ${dest.name} was incomplete")
        }
        if (!tmp.renameTo(dest)) {
            dest.delete()
            if (!tmp.renameTo(dest)) throw IOException("Couldn't save ${dest.name}")
        }
    }

    fun upload(file: File, sha256: String, progress: (Long) -> Unit = {}): InboxItem {
        val octet = "application/octet-stream".toMediaType()
        val body = object : RequestBody() {
            override fun contentType() = octet
            override fun contentLength() = file.length()
            override fun writeTo(sink: BufferedSink) {
                file.source().use { src ->
                    var total = 0L
                    while (true) {
                        val n = src.read(sink.buffer, 1L shl 16)
                        if (n < 0) break
                        total += n
                        sink.emit()
                        progress(total)
                    }
                }
            }
        }
        return call(request(url("inbox", mapOf("name" to file.name, "sha256" to sha256))).put(body).build())
    }

    fun inboxItem(id: String): InboxItem = call(request(url("inbox/$id")).get().build())

    fun inbox(): List<InboxItem> = call<InboxList>(request(url("inbox")).get().build()).items

    fun acknowledge(id: String) {
        execute(request(url("inbox/$id")).delete().build()).close()
    }

    // --- phone tools. A PC running an older Music Tools answers 404 to these.

    fun library(): LibraryResponse = call(request(url("library")).get().build())

    fun playlists(): List<Playlist> = call<PlaylistList>(request(url("playlists")).get().build()).playlists

    fun savePlaylist(name: String, tracks: List<String>) {
        val body = json.encodeToString(SavePlaylist.serializer(), SavePlaylist(tracks)).toRequestBody(jsonType)
        execute(request(playlistUrl(name)).put(body).build()).close()
    }

    fun deletePlaylist(name: String) {
        execute(request(playlistUrl(name)).delete().build()).close()
    }

    private fun playlistUrl(name: String) = url("playlists").newBuilder().addPathSegment(name).build()

    fun pipeline(since: Int = 0): PipelineStatus =
        call(request(url("pipeline", mapOf("since" to since.toString()))).get().build())

    fun startPipeline(steps: List<String>) {
        val body = json.encodeToString(StartPipeline.serializer(), StartPipeline(steps)).toRequestBody(jsonType)
        execute(request(url("pipeline")).post(body).build()).close()
    }

    fun cancelPipeline() {
        execute(request(url("pipeline/cancel")).post(ByteArray(0).toRequestBody(jsonType)).build()).close()
    }
}
