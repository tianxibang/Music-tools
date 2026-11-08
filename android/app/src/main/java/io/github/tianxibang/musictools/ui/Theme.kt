package io.github.tianxibang.musictools.ui

import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.darkColorScheme
import androidx.compose.runtime.Composable
import androidx.compose.ui.graphics.Color

/** Same palette as the desktop app (musictools/ui/theme.py). */
object Palette {
    val Bg = Color(0xFF0D0F13)
    val Card = Color(0xFF171A21)
    val Raised = Color(0xFF1E222B)
    val Border = Color(0xFF262B36)
    val Text = Color(0xFFE8EAF0)
    val Muted = Color(0xFF8A91A3)
    val Accent = Color(0xFF8B5CF6)
    val AccentHi = Color(0xFFA07BF8)
    val Ok = Color(0xFF34D399)
    val Warn = Color(0xFFFBBF24)
    val Err = Color(0xFFF87171)
}

private val colors = darkColorScheme(
    primary = Palette.Accent,
    onPrimary = Color.White,
    primaryContainer = Color(0x298B5CF6),
    onPrimaryContainer = Palette.Text,
    secondary = Palette.AccentHi,
    background = Palette.Bg,
    onBackground = Palette.Text,
    surface = Palette.Bg,
    onSurface = Palette.Text,
    surfaceVariant = Palette.Card,
    onSurfaceVariant = Palette.Muted,
    surfaceContainer = Palette.Card,
    surfaceContainerHigh = Palette.Raised,
    surfaceContainerHighest = Palette.Raised,
    surfaceContainerLow = Palette.Card,
    outline = Palette.Border,
    outlineVariant = Palette.Border,
    error = Palette.Err,
)

@Composable
fun MusicToolsTheme(content: @Composable () -> Unit) = MaterialTheme(colorScheme = colors, content = content)
