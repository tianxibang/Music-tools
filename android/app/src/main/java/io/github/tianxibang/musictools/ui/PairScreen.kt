package io.github.tianxibang.musictools.ui

import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.navigationBarsPadding
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.text.KeyboardOptions
import androidx.compose.foundation.verticalScroll
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.Computer
import androidx.compose.material.icons.filled.LockOpen
import androidx.compose.material.icons.filled.QrCodeScanner
import androidx.compose.material.icons.filled.Usb
import androidx.compose.material3.AlertDialog
import androidx.compose.material3.Button
import androidx.compose.material3.CircularProgressIndicator
import androidx.compose.material3.Icon
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
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.text.input.KeyboardCapitalization
import androidx.compose.ui.text.input.KeyboardType
import androidx.compose.ui.unit.dp
import androidx.lifecycle.compose.collectAsStateWithLifecycle
import com.journeyapps.barcodescanner.ScanContract
import com.journeyapps.barcodescanner.ScanOptions
import io.github.tianxibang.musictools.data.Prefs
import io.github.tianxibang.musictools.net.FoundPc
import io.github.tianxibang.musictools.net.Pairing
import io.github.tianxibang.musictools.net.PairingInvite
import io.github.tianxibang.musictools.net.Protocol
import io.github.tianxibang.musictools.net.discoverPcs
import io.github.tianxibang.musictools.net.usbPc
import io.github.tianxibang.musictools.sync.SyncWorker
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.flow.emptyFlow
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext

/** Where a typed code goes: a PC found on the network, or an address typed by hand. */
private data class Target(val name: String, val hosts: List<String>, val port: Int, val shortFp: String?,
                          val open: Boolean)

@Composable
fun PairScreen() {
    val context = LocalContext.current
    val prefs = remember { Prefs.get(context) }
    val scope = rememberCoroutineScope()
    var busy by remember { mutableStateOf(false) }
    var error by remember { mutableStateOf<String?>(null) }
    var target by remember { mutableStateOf<Target?>(null) }
    var manual by remember { mutableStateOf(false) }
    val discovery = remember { runCatching { discoverPcs(context) }.getOrDefault(emptyFlow()) }
    val network by discovery.collectAsStateWithLifecycle(emptyList())
    val cable by remember { usbPc() }.collectAsStateWithLifecycle(null)
    val found = listOfNotNull(cable) + network

    fun attempt(block: () -> Unit) {
        busy = true
        error = null
        scope.launch {
            val failure = withContext(Dispatchers.IO) { runCatching(block).exceptionOrNull() }
            busy = false
            if (failure == null) {
                target = null
                SyncWorker.schedule(context)
                SyncWorker.syncNow(context)
            } else {
                error = failure.message ?: failure.toString()
            }
        }
    }

    val scanner = androidx.activity.compose.rememberLauncherForActivityResult(ScanContract()) { result ->
        val text = result.contents ?: return@rememberLauncherForActivityResult
        val invite = PairingInvite.parse(text)
        if (invite == null) {
            error = "That isn't a Music Tools pairing code."
        } else {
            // The QR code carries the PC's certificate fingerprint, so it's pinned from the first byte
            attempt { Pairing.withCode(prefs, invite.hosts, invite.port, invite.code, invite.fingerprint) }
        }
    }

    Column(
        Modifier.fillMaxSize().verticalScroll(rememberScrollState()).padding(horizontal = 20.dp).navigationBarsPadding(),
        verticalArrangement = Arrangement.spacedBy(14.dp),
    ) {
        PageTitle("Connect to your PC", "Open Music Tools on your PC, go to Devices and turn on sync.")

        Section("Scan the code") {
            Hint("Click “Pair a device…” on the PC and scan the QR code it shows.")
            Button(onClick = {
                scanner.launch(ScanOptions().setDesiredBarcodeFormats(ScanOptions.QR_CODE)
                    .setPrompt("Scan the code shown on your PC").setBeepEnabled(false).setOrientationLocked(false))
            }, enabled = !busy, modifier = Modifier.fillMaxWidth()) {
                Icon(Icons.Default.QrCodeScanner, null)
                Spacer(Modifier.width(8.dp))
                Text("Scan QR code")
            }
        }

        Section("PCs nearby") {
            if (found.isEmpty()) {
                Row(verticalAlignment = Alignment.CenterVertically) {
                    CircularProgressIndicator(Modifier.size(18.dp), strokeWidth = 2.dp)
                    Spacer(Modifier.width(10.dp))
                    Hint("Looking on the network and USB… (the PC needs sync turned on)")
                }
            }
            found.forEach { pc -> FoundRow(pc) { target = Target(pc.name, listOf(pc.host), pc.port, pc.shortFingerprint, pc.open) } }
            TextButton(onClick = { manual = true }) { Text("Enter address manually") }
        }

        if (busy) {
            Row(verticalAlignment = Alignment.CenterVertically) {
                CircularProgressIndicator(Modifier.size(20.dp), strokeWidth = 2.dp)
                Spacer(Modifier.width(10.dp))
                Text("Connecting…")
            }
        }
        error?.let { Hint(it, Palette.Err) }
        Spacer(Modifier.padding(8.dp))
    }

    if (manual) {
        ManualDialog(onDismiss = { manual = false }) { host, port ->
            manual = false
            target = Target(host, listOf(host), port, null, false)
        }
    }
    target?.let { t ->
        CodeDialog(t, busy, error, onDismiss = { target = null; error = null },
            onOpen = { attempt { Pairing.open(prefs, t.hosts.first(), t.port) } },
            onCode = { code -> attempt { Pairing.withCode(prefs, t.hosts, t.port, code, null, t.shortFp) } })
    }
}

@Composable
private fun FoundRow(pc: FoundPc, onClick: () -> Unit) {
    Row(Modifier.fillMaxWidth().clickable(onClick = onClick).padding(vertical = 8.dp),
        verticalAlignment = Alignment.CenterVertically) {
        Icon(when {
            pc.usb -> Icons.Default.Usb
            pc.open -> Icons.Default.LockOpen
            else -> Icons.Default.Computer
        }, null, tint = Palette.AccentHi)
        Spacer(Modifier.width(12.dp))
        Column(Modifier.weight(1f)) {
            Text(pc.name)
            Hint((if (pc.usb) "via USB cable" else "${pc.host}:${pc.port}") + if (pc.open) " · no pairing needed" else "")
        }
    }
}

@Composable
private fun ManualDialog(onDismiss: () -> Unit, onDone: (String, Int) -> Unit) {
    var host by remember { mutableStateOf("") }
    var port by remember { mutableStateOf(Protocol.DEFAULT_PORT.toString()) }
    AlertDialog(
        onDismissRequest = onDismiss,
        title = { Text("PC address") },
        text = {
            Column(verticalArrangement = Arrangement.spacedBy(8.dp)) {
                Hint("Shown on the PC's Devices page, e.g. 192.168.1.20")
                OutlinedTextField(host, { host = it.trim() }, label = { Text("Address") }, singleLine = true,
                    keyboardOptions = KeyboardOptions(keyboardType = KeyboardType.Uri))
                OutlinedTextField(port, { port = it.filter(Char::isDigit) }, label = { Text("Port") }, singleLine = true,
                    keyboardOptions = KeyboardOptions(keyboardType = KeyboardType.Number))
            }
        },
        confirmButton = {
            TextButton(onClick = { onDone(host, port.toIntOrNull() ?: Protocol.DEFAULT_PORT) },
                enabled = host.isNotBlank()) { Text("Next") }
        },
        dismissButton = { TextButton(onClick = onDismiss) { Text("Cancel") } },
    )
}

@Composable
private fun CodeDialog(t: Target, busy: Boolean, error: String?, onDismiss: () -> Unit, onOpen: () -> Unit,
                       onCode: (String) -> Unit) {
    var code by remember { mutableStateOf("") }
    AlertDialog(
        onDismissRequest = onDismiss,
        title = { Text(t.name) },
        text = {
            Column(verticalArrangement = Arrangement.spacedBy(8.dp)) {
                if (t.open) {
                    Hint("This PC lets devices on your network sync without pairing.")
                } else {
                    Hint("Click “Pair a device…” on the PC and type the code it shows.")
                }
                OutlinedTextField(code, { code = it.uppercase().take(14) }, label = { Text("Pairing code") },
                    placeholder = { Text("XXXX-XXXX-XXXX") }, singleLine = true, enabled = !busy,
                    keyboardOptions = KeyboardOptions(capitalization = KeyboardCapitalization.Characters,
                        keyboardType = KeyboardType.Ascii))
                if (busy) Hint("Connecting…")
                error?.let { Hint(it, Palette.Err) }
            }
        },
        confirmButton = {
            TextButton(onClick = { onCode(code) }, enabled = !busy && Protocol.normalizeCode(code).length == 12) {
                Text("Pair")
            }
        },
        dismissButton = {
            Row {
                if (t.open) OutlinedButton(onClick = onOpen, enabled = !busy) { Text("Connect without code") }
                TextButton(onClick = onDismiss) { Text("Cancel") }
            }
        },
    )
}
