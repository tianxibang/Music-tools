package io.github.tianxibang.musictools.update

import android.content.Context
import android.content.Intent
import android.net.Uri
import android.provider.Settings
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.setValue
import androidx.core.content.FileProvider
import kotlinx.serialization.json.Json
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.jsonArray
import kotlinx.serialization.json.jsonObject
import kotlinx.serialization.json.jsonPrimitive
import okhttp3.OkHttpClient
import okhttp3.Request
import java.io.File
import java.io.IOException
import java.security.MessageDigest
import java.util.concurrent.TimeUnit

/** New versions of the app from GitHub releases: the same release as the desktop app, which ships the APK. */
object Updater {
    private const val LATEST = "https://api.github.com/repos/tianxibang/Music-tools/releases/latest"
    private const val APK = "MusicTools-android.apk"
    private const val CHECK_EVERY_MS = 12 * 3600 * 1000L

    data class Release(val tag: String, val version: String, val name: String, val notes: String,
                       val apkUrl: String, val digest: String)

    /** What the update dialog shows: the release on offer, and the download while it runs. */
    var offer by mutableStateOf<Release?>(null)
    var downloading by mutableStateOf<Float?>(null)
    var error by mutableStateOf<String?>(null)

    private val http = OkHttpClient.Builder().connectTimeout(15, TimeUnit.SECONDS)
        .readTimeout(60, TimeUnit.SECONDS).build()

    fun currentVersion(context: Context): String =
        context.packageManager.getPackageInfo(context.packageName, 0).versionName ?: "0"

    /** Debug builds have their own app id, so a release APK can't update them. */
    fun canUpdate(context: Context) = !context.packageName.endsWith(".debug")

    fun parseVersion(text: String): List<Int> =
        Regex("""^v?(\d+(?:\.\d+)*)""").find(text.trim())?.groupValues?.get(1)?.split('.')?.map { it.toInt() }
            ?: emptyList()

    fun newer(tag: String, current: String): Boolean {
        val a = parseVersion(tag)
        val b = parseVersion(current)
        for (i in 0 until maxOf(a.size, b.size)) {
            val x = a.getOrElse(i) { 0 }
            val y = b.getOrElse(i) { 0 }
            if (x != y) return x > y
        }
        return false
    }

    /** The release notes without the downloads table and with the Markdown marks a plain dialog can't show. */
    fun plainNotes(markdown: String): String = markdown.substringBefore("\n---\n").lines()
        .filterNot { it.startsWith("# ") || it.startsWith("*Music Tools ") }
        .joinToString("\n") { line ->
            line.removePrefix("### ").replace("**", "").replace("`", "")
                .replace(Regex("""\[([^\]]+)]\([^)]+\)"""), "$1").replace(Regex("""^- """), "• ")
        }.trim()

    fun parse(json: String): Release? {
        val root = Json.parseToJsonElement(json).jsonObject
        val tag = root["tag_name"]?.jsonPrimitive?.content ?: return null
        val apk = root["assets"]?.jsonArray?.map { it.jsonObject }
            ?.firstOrNull { it.str("name") == APK } ?: return null
        return Release(tag, tag.removePrefix("v"), root.str("name") ?: tag, root.str("body") ?: "",
            apk.str("browser_download_url") ?: return null, apk.str("digest") ?: "")
    }

    private fun JsonObject.str(key: String) = this[key]?.takeIf { it !is kotlinx.serialization.json.JsonNull }
        ?.jsonPrimitive?.content

    /** Blocking. The latest release if it is newer than this app, else null. Network errors throw. */
    fun check(context: Context): Release? {
        val req = Request.Builder().url(LATEST).header("Accept", "application/vnd.github+json").build()
        val body = http.newCall(req).execute().use { r ->
            if (!r.isSuccessful) throw IOException("GitHub answered ${r.code}")
            r.body!!.string()
        }
        prefs(context).edit().putLong("update_checked", System.currentTimeMillis()).apply()
        return parse(body)?.takeIf { newer(it.tag, currentVersion(context)) }
    }

    /** Blocking, at most every 12 hours: offer a new version unless the user skipped it. */
    fun checkInBackground(context: Context) {
        if (!canUpdate(context)) return
        if (System.currentTimeMillis() - prefs(context).getLong("update_checked", 0) < CHECK_EVERY_MS) return
        val rel = runCatching { check(context) }.getOrNull() ?: return
        if (rel.tag != prefs(context).getString("update_skip", null)) offer = rel
    }

    fun skip(context: Context, rel: Release) {
        prefs(context).edit().putString("update_skip", rel.tag).apply()
        offer = null
    }

    /** Blocking: download the APK into the cache, checking it against the release's SHA-256. */
    fun download(context: Context, rel: Release, progress: (Float) -> Unit): File {
        val dir = File(context.cacheDir, "updates").apply { mkdirs() }
        dir.listFiles()?.forEach { it.delete() }
        val file = File(dir, "MusicTools-${rel.version}.apk")
        val sha = MessageDigest.getInstance("SHA-256")
        http.newCall(Request.Builder().url(rel.apkUrl).build()).execute().use { r ->
            if (!r.isSuccessful) throw IOException("Download failed (${r.code})")
            val body = r.body!!
            val total = body.contentLength()
            var done = 0L
            body.byteStream().use { input ->
                file.outputStream().use { out ->
                    val buf = ByteArray(1 shl 16)
                    while (true) {
                        val n = input.read(buf)
                        if (n < 0) break
                        out.write(buf, 0, n)
                        sha.update(buf, 0, n)
                        done += n
                        if (total > 0) progress(done.toFloat() / total)
                    }
                }
            }
        }
        val hex = sha.digest().joinToString("") { "%02x".format(it) }
        if (rel.digest.startsWith("sha256:") && rel.digest.removePrefix("sha256:") != hex) {
            file.delete()
            throw IOException("The download doesn't match the release's checksum. Try again later.")
        }
        return file
    }

    /** Hand the APK to Android's installer, which asks the user to confirm. Android only allows that after
     *  "Install unknown apps" is on for this app: until then this opens that setting instead (returns false). */
    fun install(context: Context, apk: File): Boolean {
        if (!context.packageManager.canRequestPackageInstalls()) {
            context.startActivity(Intent(Settings.ACTION_MANAGE_UNKNOWN_APP_SOURCES,
                Uri.parse("package:${context.packageName}")).addFlags(Intent.FLAG_ACTIVITY_NEW_TASK))
            return false
        }
        val uri = FileProvider.getUriForFile(context, "${context.packageName}.files", apk)
        context.startActivity(Intent(Intent.ACTION_VIEW).setDataAndType(uri, "application/vnd.android.package-archive")
            .addFlags(Intent.FLAG_GRANT_READ_URI_PERMISSION or Intent.FLAG_ACTIVITY_NEW_TASK))
        return true
    }

    private fun prefs(context: Context) = context.getSharedPreferences("musictools", Context.MODE_PRIVATE)
}
