package io.github.tianxibang.musictools.ui

import android.text.format.DateUtils
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
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
import androidx.compose.material.icons.filled.Computer
import androidx.compose.material.icons.filled.LockOpen
import androidx.compose.material.icons.filled.Stop
import androidx.compose.material.icons.filled.Sync
import androidx.compose.material3.Button
import androidx.compose.material3.Icon
import androidx.compose.material3.LinearProgressIndicator
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedButton
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.remember
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.text.font.FontFamily
import androidx.compose.ui.unit.dp
import androidx.lifecycle.compose.collectAsStateWithLifecycle
import io.github.tianxibang.musictools.data.Prefs
import io.github.tianxibang.musictools.sync.AUTO_RETRIES
import io.github.tianxibang.musictools.sync.Level
import io.github.tianxibang.musictools.sync.SyncLog
import io.github.tianxibang.musictools.sync.SyncWorker

@Composable
fun SyncScreen() {
    val context = LocalContext.current
    val prefs by remember { Prefs.get(context) }.state.collectAsStateWithLifecycle()
    val sync by remember { SyncLog.get(context) }.state.collectAsStateWithLifecycle()
    val pc = prefs.pc ?: return

    LazyColumn(
        Modifier.fillMaxSize().padding(horizontal = 20.dp),
        verticalArrangement = Arrangement.spacedBy(14.dp),
    ) {
        item { PageTitle("Sync") }
        item {
            Section {
                Row(verticalAlignment = Alignment.CenterVertically) {
                    Icon(if (pc.token == null) Icons.Default.LockOpen else Icons.Default.Computer, null,
                        tint = Palette.AccentHi)
                    Spacer(Modifier.width(12.dp))
                    Column(Modifier.weight(1f)) {
                        Text(pc.name, style = MaterialTheme.typography.titleMedium)
                        Hint(if (pc.token == null) "Open mode · encrypted" else "Paired · encrypted")
                    }
                }
                when {
                    sync.running -> {
                        Text(sync.status)
                        val p = when {
                            sync.totalBytes > 0 -> sync.bytes.toFloat() / sync.totalBytes
                            sync.steps > 0 -> sync.step.toFloat() / sync.steps
                            else -> null
                        }
                        if (p != null) LinearProgressIndicator({ p }, Modifier.fillMaxWidth())
                        else LinearProgressIndicator(Modifier.fillMaxWidth())
                    }
                    sync.lastRun > 0 -> {
                        val ago = if (System.currentTimeMillis() - sync.lastRun < 60_000) "just now"
                        else DateUtils.getRelativeTimeSpanString(sync.lastRun).toString()
                        if (sync.lastError.isNotEmpty()) {
                            Hint("Last sync $ago failed", Palette.Err)
                            Text(sync.lastError, style = MaterialTheme.typography.bodyMedium)
                        } else {
                            Hint("Last sync $ago")
                            Text(sync.lastSummary, style = MaterialTheme.typography.bodyMedium)
                        }
                    }
                    else -> Hint("Not synced yet.")
                }
                if (sync.running) {
                    OutlinedButton(onClick = { SyncWorker.stop(context) }, Modifier.fillMaxWidth()) {
                        Icon(Icons.Default.Stop, null)
                        Spacer(Modifier.width(8.dp))
                        Text("Stop")
                    }
                } else {
                    Button(onClick = { SyncWorker.syncNow(context) }, Modifier.fillMaxWidth()) {
                        Icon(Icons.Default.Sync, null)
                        Spacer(Modifier.width(8.dp))
                        Text("Sync now")
                    }
                }
            }
        }
        if (sync.failed.isNotEmpty()) {
            item {
                Section("Couldn't sync") {
                    sync.failed.forEach { f ->
                        Column {
                            Text(f.name, style = MaterialTheme.typography.bodyMedium)
                            Hint(f.error + if (f.attempts > 1) " (${f.attempts} tries)" else "", Palette.Err)
                        }
                    }
                    Hint(if (sync.failed.any { it.attempts >= AUTO_RETRIES })
                        "Tap “Sync now” to try again. Automatic syncs skip a file after $AUTO_RETRIES failed tries."
                    else "These are tried again on the next sync, or tap “Sync now”.")
                }
            }
        }
        item {
            Section("How it works") {
                Hint("• New FLACs in ${prefs.settings.sourceDirs.joinToString { it.substringAfterLast('/') }} " +
                    "go to your PC's library.")
                Hint("• The PC keeps the FLAC and sends back an Opus version" +
                    if (prefs.settings.deleteFlacAfterSync) ", which replaces the FLAC here." else ".")
                Hint("• ${prefs.settings.musicDir.substringAfterLast("/0/")} mirrors the PC's Opus library.")
                if (prefs.settings.autoSync) Hint("• Syncs automatically every 30 minutes on Wi-Fi.")
            }
        }
        if (sync.lines.isNotEmpty()) {
            item { Text("Activity", style = MaterialTheme.typography.titleSmall, color = Palette.Muted) }
            items(sync.lines.asReversed()) { line ->
                Text(
                    line.text,
                    style = MaterialTheme.typography.bodySmall.copy(fontFamily = FontFamily.Monospace),
                    color = when (line.level) {
                        Level.OK -> Palette.Ok
                        Level.WARN -> Palette.Warn
                        Level.ERROR -> Palette.Err
                        Level.INFO -> Palette.Text
                    },
                )
            }
        }
        item { Spacer(Modifier.height(12.dp)) }
    }
}
