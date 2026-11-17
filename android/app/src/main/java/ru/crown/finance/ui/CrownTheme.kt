package ru.crown.finance.ui

import androidx.compose.foundation.isSystemInDarkTheme
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.darkColorScheme
import androidx.compose.material3.lightColorScheme
import androidx.compose.runtime.Composable
import androidx.compose.ui.graphics.Color

private val LightColors = lightColorScheme(
    primary = Color(0xFF12634B),
    onPrimary = Color.White,
    primaryContainer = Color(0xFFD8EEE4),
    onPrimaryContainer = Color(0xFF133D31),
    secondary = Color(0xFF456071),
    secondaryContainer = Color(0xFFE0EBEF),
    onSecondaryContainer = Color(0xFF2B4550),
    background = Color(0xFFF5F7F6),
    onBackground = Color(0xFF192C29),
    surface = Color(0xFFFCFDFC),
    onSurface = Color(0xFF192C29),
    surfaceVariant = Color(0xFFE6EEEA),
    onSurfaceVariant = Color(0xFF4C615A),
    outlineVariant = Color(0xFFD5E0DA),
)

private val DarkColors = darkColorScheme(
    primary = Color(0xFF9DDBBF),
    onPrimary = Color(0xFF103B2C),
    primaryContainer = Color(0xFF24483B),
    onPrimaryContainer = Color(0xFFC9EDDC),
    secondary = Color(0xFFBAD4E0),
    secondaryContainer = Color(0xFF2B4049),
    onSecondaryContainer = Color(0xFFDBEDF6),
    background = Color(0xFF151D20),
    onBackground = Color(0xFFE6EDE9),
    surface = Color(0xFF20292C),
    onSurface = Color(0xFFE6EDE9),
    surfaceVariant = Color(0xFF293B36),
    onSurfaceVariant = Color(0xFFB3C6BD),
    outlineVariant = Color(0xFF3D5148),
)

@Composable
internal fun CrownTheme(content: @Composable () -> Unit) {
    MaterialTheme(colorScheme = if (isSystemInDarkTheme()) DarkColors else LightColors, content = content)
}
