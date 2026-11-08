package io.github.tianxibang.musictools.ui

import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.PlayArrow
import androidx.compose.material.icons.filled.Stop
import androidx.compose.material3.Button
import androidx.compose.material3.Checkbox
import androidx.compose.material3.Icon
import androidx.compose.material3.LinearProgressIndicator
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedButton
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateListOf
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberCoroutineScope
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.text.font.FontFamily
import androidx.compose.ui.unit.dp
import androidx.lifecycle.compose.collectAsStateWithLifecycle
import io.github.tianxibang.musictools.data.LibraryCache
import io.github.tianxibang.musictools.data.Prefs
import io.github.tianxibang.musictools.net.Connection
import io.github.tianxibang.musictools.net.PipelineLine
import io.github.tianxibang.musictools.net.PipelineStatus
import kotlinx.coroutines.delay
import kotlinx.coroutines.launch

/** Run the desktop's one-click pipeline from the phone and follow its log. */
@Composable
fun PcScreen() {
    val context = LocalContext.current
    val prefs by remember { Prefs.get(context) }.state.collectAsStateWithLifecycle()
    val snap by remember { LibraryCache.get(context) }.state.collectAsStateWithLifecycle()
    val scope = rememberCoroutineScope()
    var status by remember { mutableStateOf<PipelineStatus?>(null) }
    var error by remember { mutableStateOf<String?>(null) }
    var chosen by remember { mutableStateOf<Set<String>?>(null) }
    val lines = remember { mutableStateListOf<PipelineLine>() }
    var runStarted by remember { mutableStateOf(0.0) }
    var next by remember { mutableStateOf(0) }
    var poke by remember { mutableStateOf(0) }  // bumped to poll right away after an action

    // Poll while the screen is open: every second while the pipeline runs, otherwise every few seconds
    LaunchedEffect(poke) {
        while (true) {
            try {
                val st = Connection.use(context) { it.pipeline(next) }
                if (st.started != runStarted) {  // a new run: start the log over
                    runStarted = st.started
                    lines.clear()
                    val full = if (next > 0) Connection.use(context) { it.pipeline(0) } else st
                    lines.addAll(full.lines)
                    next = full.next
                } else {
                    lines.addAll(st.lines)
                    next = st.next
                }
                status = st
                if (chosen == null) chosen = st.steps.filter { it.default }.map { it.key }.toSet()
                error = null
            } catch (e: Exception) {
                error = e.message ?: e.toString()
            }
            delay(if (status?.running == true) 1000 else 4000)
        }
    }

    val st = status
    LazyColumn(Modifier.fillMaxSize().padding(horizontal = 20.dp), verticalArrangement = Arrangement.spacedBy(14.dp)) {
        item { PageTitle("PC tools", prefs.pc?.name?.let { "Run the one-click pipeline on $it." }) }
        error?.let { item { Hint(it, Palette.Err) } }
        val pending = snap.library.summary.pending
        if (pending > 0) item { Hint("$pending FLAC${if (pending == 1) "" else "s"} on the PC waiting to be converted.") }

        if (st != null) {
            item {
                Section("Pipeline") {
                    st.steps.forEach { step ->
                        val on = chosen?.contains(step.key) == true
                        Row(Modifier.fillMaxWidth().clickable(enabled = !st.running) {
                            chosen = (chosen ?: emptySet()).let { if (on) it - step.key else it + step.key }
                        }, verticalAlignment = Alignment.CenterVertically) {
                            Checkbox(on, null, enabled = !st.running)
                            Spacer(Modifier.width(4.dp))
                            Text(step.label)
                        }
                    }
                    when {
                        st.running -> {
                            Text(st.progress.text.ifEmpty { "Running…" })
                            if (st.progress.total > 0) {
                                LinearProgressIndicator({ st.progress.done.toFloat() / st.progress.total }, Modifier.fillMaxWidth())
                            } else LinearProgressIndicator(Modifier.fillMaxWidth())
                            OutlinedButton(onClick = {
                                scope.launch { runCatching { Connection.use(context) { it.cancelPipeline() } }; poke++ }
                            }, Modifier.fillMaxWidth()) {
                                Icon(Icons.Default.Stop, null)
                                Spacer(Modifier.width(8.dp))
                                Text("Stop")
                            }
                        }
                        st.busy -> Hint("The PC is busy with a job started on the PC. Try again when it's done.")
                        else -> Button(onClick = {
                            val steps = st.steps.map { it.key }.filter { chosen?.contains(it) == true }
                            scope.launch {
                                error = runCatching { Connection.use(context) { it.startPipeline(steps) } }
                                    .exceptionOrNull()?.message
                                poke++
                            }
                        }, Modifier.fillMaxWidth(), enabled = chosen?.isNotEmpty() == true) {
                            Icon(Icons.Default.PlayArrow, null)
                            Spacer(Modifier.width(8.dp))
                            Text("Run on PC")
                        }
                    }
                    if (!st.running && st.finished > 0) {
                        Hint((if (st.error.isNotEmpty()) "Last run failed: ${st.error}" else "Last run finished") +
                            (if (st.startedBy.isNotEmpty()) " · started from ${st.startedBy}" else ""),
                            if (st.error.isNotEmpty()) Palette.Err else Palette.Muted)
                    }
                }
            }
        } else if (error == null) {
            item { Hint("Connecting to your PC…") }
        }

        if (lines.isNotEmpty()) {
            item { Text("Log", style = MaterialTheme.typography.titleSmall, color = Palette.Muted) }
            items(lines.asReversed()) { line ->
                Text(
                    line.text,
                    style = MaterialTheme.typography.bodySmall.copy(fontFamily = FontFamily.Monospace),
                    color = when (line.level) {
                        "ok" -> Palette.Ok
                        "warn" -> Palette.Warn
                        "error" -> Palette.Err
                        "head" -> Palette.AccentHi
                        else -> Palette.Text
                    },
                )
            }
        }
        item { Spacer(Modifier.height(12.dp)) }
    }
}
