package io.github.tianxibang.musictools.ui

import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.verticalScroll
import androidx.compose.material3.AlertDialog
import androidx.compose.material3.ButtonDefaults
import androidx.compose.material3.OutlinedButton
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberCoroutineScope
import androidx.compose.runtime.setValue
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.unit.dp
import androidx.lifecycle.compose.collectAsStateWithLifecycle
import io.github.tianxibang.musictools.data.Prefs
import io.github.tianxibang.musictools.net.Pairing
import io.github.tianxibang.musictools.sync.SyncWorker
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext

@Composable
fun SettingsScreen() {
    val context = LocalContext.current
    val prefsStore = remember { Prefs.get(context) }
    val prefs by prefsStore.state.collectAsStateWithLifecycle()
    val s = prefs.settings
    val scope = rememberCoroutineScope()
    var confirmUnpair by remember { mutableStateOf(false) }
    var musicDir by remember { mutableStateOf(s.musicDir) }
    var sources by remember { mutableStateOf(s.sourceDirs.joinToString("\n")) }
    var name by remember { mutableStateOf(prefs.deviceName) }

    fun update(reschedule: Boolean = false, block: (io.github.tianxibang.musictools.data.Settings) ->
        io.github.tianxibang.musictools.data.Settings) {
        prefsStore.update(block)
        if (reschedule) SyncWorker.schedule(context)
    }

    Column(
        Modifier.fillMaxSize().verticalScroll(rememberScrollState()).padding(horizontal = 20.dp),
        verticalArrangement = Arrangement.spacedBy(14.dp),
    ) {
        PageTitle("Settings")

        Section("Syncing") {
            SwitchRow("Sync automatically", "About every 30 minutes, when the PC is reachable", s.autoSync) { v ->
                update(true) { it.copy(autoSync = v) }
            }
            SwitchRow("Only on Wi-Fi", "Never use mobile data", s.wifiOnly) { v ->
                update(true) { it.copy(wifiOnly = v) }
            }
            SwitchRow("Replace FLACs with Opus", "Once the PC has the FLAC and the Opus is on this phone, " +
                "delete the FLAC here to save space", s.deleteFlacAfterSync) { v ->
                update { it.copy(deleteFlacAfterSync = v) }
            }
            SwitchRow("Keep extra files", "Don't delete songs from the phone that are no longer on the PC",
                s.keepExtraFiles) { v -> update { it.copy(keepExtraFiles = v) } }
        }

        Section("Folders") {
            OutlinedTextField(musicDir, { musicDir = it }, Modifier.fillMaxWidth(), label = { Text("Music folder") },
                supportingText = { Text("Mirrors the PC's Opus library") }, singleLine = true)
            OutlinedTextField(sources, { sources = it }, Modifier.fillMaxWidth(),
                label = { Text("Look for new FLACs in") }, supportingText = { Text("One folder per line") },
                minLines = 2)
            val changed = musicDir.trim() != s.musicDir || sources.trim() != s.sourceDirs.joinToString("\n")
            if (changed) {
                TextButton(onClick = {
                    update {
                        it.copy(musicDir = musicDir.trim().trimEnd('/'),
                            sourceDirs = sources.lines().map { l -> l.trim().trimEnd('/') }.filter(String::isNotEmpty))
                    }
                }) { Text("Save folders") }
            }
        }

        Section("This phone") {
            OutlinedTextField(name, { name = it }, Modifier.fillMaxWidth(), label = { Text("Name shown on the PC") },
                singleLine = true)
            if (name.trim() != prefs.deviceName && name.isNotBlank()) {
                TextButton(onClick = { prefsStore.setDeviceName(name) }) { Text("Save name (used when pairing)") }
            }
        }

        prefs.pc?.let { pc ->
            Section("PC") {
                Text(pc.name)
                Hint(pc.hosts.joinToString { "$it:${pc.port}" })
                Hint("Certificate ${pc.fingerprint.take(4).uppercase()}…${pc.fingerprint.takeLast(4).uppercase()}" +
                    if (pc.token == null) " · open mode" else " · paired")
                OutlinedButton(onClick = { confirmUnpair = true },
                    colors = ButtonDefaults.outlinedButtonColors(contentColor = Palette.Err)) {
                    Text(if (pc.token == null) "Disconnect" else "Unpair")
                }
            }
        }
        Spacer(Modifier.height(12.dp))
    }

    if (confirmUnpair) {
        AlertDialog(
            onDismissRequest = { confirmUnpair = false },
            title = { Text("Unpair from ${prefs.pc?.name}?") },
            text = { Text("Your music stays on the phone. To sync again you'll need a new code from the PC.") },
            confirmButton = {
                TextButton(onClick = {
                    confirmUnpair = false
                    SyncWorker.stop(context)
                    scope.launch {
                        withContext(Dispatchers.IO) { Pairing.unpair(prefsStore) }
                        SyncWorker.schedule(context)
                    }
                }) { Text("Unpair", color = Palette.Err) }
            },
            dismissButton = { TextButton(onClick = { confirmUnpair = false }) { Text("Cancel") } },
        )
    }
}
