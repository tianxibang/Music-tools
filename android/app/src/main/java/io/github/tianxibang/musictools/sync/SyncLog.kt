package io.github.tianxibang.musictools.sync

import android.content.Context
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.flow.update

enum class Level { INFO, OK, WARN, ERROR }

data class LogLine(val time: Long, val text: String, val level: Level)

data class SyncState(
    val running: Boolean = false,
    val status: String = "",
    val step: Int = 0,
    val steps: Int = 0,
    /** Bytes of the current file, for the progress bar. */
    val bytes: Long = 0,
    val totalBytes: Long = 0,
    val lastRun: Long = 0,
    val lastSummary: String = "",
    val lastError: String = "",
    val lines: List<LogLine> = emptyList(),
)

/** Shared sync state: written by the worker, shown by the UI and the notification. */
class SyncLog private constructor(context: Context) {
    private val sp = context.getSharedPreferences("sync_log", Context.MODE_PRIVATE)
    private val _state = MutableStateFlow(
        SyncState(lastRun = sp.getLong("last_run", 0), lastSummary = sp.getString("last_summary", "")!!,
            lastError = sp.getString("last_error", "")!!)
    )
    val state: StateFlow<SyncState> = _state.asStateFlow()
    var onChange: ((SyncState) -> Unit)? = null

    private fun set(block: (SyncState) -> SyncState) {
        _state.update(block)
        onChange?.invoke(_state.value)
    }

    fun start() = set { it.copy(running = true, status = "Starting…", step = 0, steps = 0, lines = emptyList()) }

    fun status(text: String, step: Int = 0, steps: Int = 0) =
        set { it.copy(status = text, step = step, steps = steps, bytes = 0, totalBytes = 0) }

    fun progress(bytes: Long, total: Long) = _state.update { it.copy(bytes = bytes, totalBytes = total) }

    fun line(text: String, level: Level = Level.INFO) =
        set { it.copy(lines = (it.lines + LogLine(System.currentTimeMillis(), text, level)).takeLast(300)) }

    fun finish(summary: String?, error: String?) {
        val now = System.currentTimeMillis()
        sp.edit().putLong("last_run", now).putString("last_summary", summary ?: "")
            .putString("last_error", error ?: "").apply()
        set { it.copy(running = false, status = "", lastRun = now, lastSummary = summary ?: "", lastError = error ?: "") }
    }

    companion object {
        @Volatile private var instance: SyncLog? = null
        fun get(context: Context) = instance ?: synchronized(this) {
            instance ?: SyncLog(context.applicationContext).also { instance = it }
        }
    }
}
