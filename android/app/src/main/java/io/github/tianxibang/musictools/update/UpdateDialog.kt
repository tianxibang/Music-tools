package io.github.tianxibang.musictools.update

import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.heightIn
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.verticalScroll
import androidx.compose.material3.AlertDialog
import androidx.compose.material3.LinearProgressIndicator
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.rememberCoroutineScope
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.unit.dp
import io.github.tianxibang.musictools.ui.Hint
import io.github.tianxibang.musictools.ui.Palette
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext

/** Looks for a new version when the app opens, and asks about it: Update / Later / Skip. */
@Composable
fun UpdatePrompt() {
    val context = LocalContext.current
    val scope = rememberCoroutineScope()
    LaunchedEffect(Unit) { withContext(Dispatchers.IO) { Updater.checkInBackground(context) } }
    val rel = Updater.offer ?: return
    val busy = Updater.downloading != null

    AlertDialog(
        onDismissRequest = { if (!busy) Updater.offer = null },
        title = { Text("Music Tools ${rel.version} is available") },
        text = {
            Column(Modifier.heightIn(max = 420.dp).verticalScroll(rememberScrollState())) {
                Hint("You have ${Updater.currentVersion(context)}. Android asks you to confirm the update.")
                Updater.downloading?.let { LinearProgressIndicator({ it }, Modifier.fillMaxWidth().padding(top = 10.dp)) }
                Updater.error?.let { Hint(it, Palette.Err) }
                Text(Updater.plainNotes(rel.notes), Modifier.padding(top = 10.dp))
            }
        },
        confirmButton = {
            TextButton(enabled = !busy, onClick = {
                Updater.error = null
                Updater.downloading = 0f
                scope.launch {
                    val res = withContext(Dispatchers.IO) {
                        runCatching { Updater.download(context, rel) { p -> Updater.downloading = p } }
                    }
                    Updater.downloading = null
                    res.onSuccess { apk ->
                        if (Updater.install(context, apk)) Updater.offer = null
                        else Updater.error = "Allow “Install unknown apps” for Music Tools, then press Update again."
                    }.onFailure { Updater.error = it.message ?: "The download failed." }
                }
            }) { Text(if (busy) "Downloading…" else "Update") }
        },
        dismissButton = {
            Row {
                TextButton(enabled = !busy, onClick = { Updater.skip(context, rel) }) { Text("Skip") }
                TextButton(enabled = !busy, onClick = { Updater.offer = null }) { Text("Later") }
            }
        },
    )
}
