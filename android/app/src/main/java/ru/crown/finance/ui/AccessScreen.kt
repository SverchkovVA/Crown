package ru.crown.finance.ui

import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.imePadding
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.safeDrawingPadding
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.text.KeyboardOptions
import androidx.compose.foundation.verticalScroll
import androidx.compose.material3.Button
import androidx.compose.material3.LinearProgressIndicator
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedButton
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.Surface
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Modifier
import androidx.compose.ui.semantics.LiveRegionMode
import androidx.compose.ui.semantics.heading
import androidx.compose.ui.semantics.liveRegion
import androidx.compose.ui.semantics.semantics
import androidx.compose.ui.text.input.KeyboardType
import androidx.compose.ui.text.input.PasswordVisualTransformation
import androidx.compose.ui.unit.dp
import ru.crown.finance.Workspace

@Composable
internal fun AccessScreen(
    state: Workspace.Access,
    create: (String, CharArray) -> Unit,
    open: (CharArray, Boolean) -> Unit,
    demo: () -> Unit,
) {
    var title by remember { mutableStateOf("") }
    var secret by remember { mutableStateOf("") }
    var confirmation by remember { mutableStateOf("") }
    var recovery by remember { mutableStateOf(false) }
    var mismatch by remember { mutableStateOf(false) }
    WorkspaceSurface {
        Text("Crown", color = MaterialTheme.colorScheme.primary, style = MaterialTheme.typography.titleLarge)
        val action = if (state.exists) "Открыть книгу" else "Создать книгу"
        WorkspaceTitle(if (state.exists) "Финансовая книга" else "Новая книга")
        if (!state.exists) {
            Entry(title, { title = it.take(100) }, "Название книги", state.busy)
        }
        SecretField(
            secret, {
                secret = it
                mismatch = false
            },
            if (recovery) "Код восстановления CA1" else "Парольная фраза", state.busy,
        )
        if (!state.exists) {
            SecretField(
                confirmation, {
                    confirmation = it
                    mismatch = false
                },
                "Повторите парольную фразу", state.busy,
            )
        }
        if (mismatch) FormError("Парольные фразы различаются")
        state.error?.let { FormError(it) }
        Button(
            onClick = {
                if (!state.exists && secret != confirmation) {
                    mismatch = true
                } else {
                    val credential = secret.toCharArray()
                    secret = ""
                    confirmation = ""
                    if (state.exists) open(credential, recovery) else create(title, credential)
                }
            },
            enabled = !state.busy && secret.isNotEmpty() && (state.exists || title.isNotBlank()),
            modifier = Modifier.fillMaxWidth(),
        ) { Text(action) }
        if (state.busy) LinearProgressIndicator(Modifier.fillMaxWidth())
        if (state.exists) {
            TextButton(
                onClick = {
                    recovery = !recovery
                    secret = ""
                },
                enabled = !state.busy,
            ) {
                Text(if (recovery) "Ввести парольную фразу" else "Войти по коду восстановления")
            }
        }
        OutlinedButton(demo, Modifier.fillMaxWidth(), enabled = !state.busy) { Text("Учебная книга") }
    }
}

@Composable
internal fun RecoveryScreen(code: String, acknowledge: () -> Unit) {
    WorkspaceSurface {
        WorkspaceTitle("Код восстановления")
        Text("Запишите код. Он позволит открыть книгу на этом устройстве, если вы забудете парольную фразу.")
        Surface(color = MaterialTheme.colorScheme.secondaryContainer, shape = MaterialTheme.shapes.medium) {
            Text(
                code, Modifier.fillMaxWidth().padding(16.dp),
                style = MaterialTheme.typography.bodyLarge,
                color = MaterialTheme.colorScheme.onSecondaryContainer,
            )
        }
        Button(acknowledge, Modifier.fillMaxWidth()) { Text("Записал код") }
    }
}

@Composable
private fun WorkspaceSurface(content: @Composable () -> Unit) {
    Surface(Modifier.fillMaxSize(), color = MaterialTheme.colorScheme.background) {
        Column(
            Modifier.fillMaxSize().safeDrawingPadding().imePadding()
                .verticalScroll(rememberScrollState()).padding(horizontal = 24.dp, vertical = 20.dp),
            verticalArrangement = Arrangement.spacedBy(14.dp),
        ) { content() }
    }
}

@Composable
internal fun WorkspaceTitle(title: String) {
    Text(title, Modifier.semantics { heading() }, style = MaterialTheme.typography.headlineSmall)
}

@Composable
internal fun FormError(message: String) {
    Text(
        message, Modifier.semantics { liveRegion = LiveRegionMode.Polite },
        color = MaterialTheme.colorScheme.error, style = MaterialTheme.typography.bodyMedium,
    )
}

@Composable
internal fun Entry(
    value: String,
    change: (String) -> Unit,
    label: String,
    busy: Boolean,
    keyboard: KeyboardType = KeyboardType.Text,
) {
    OutlinedTextField(
        value, change, Modifier.fillMaxWidth(), label = { Text(label) },
        enabled = !busy, singleLine = true, keyboardOptions = KeyboardOptions(keyboardType = keyboard),
    )
}

@Composable
private fun SecretField(value: String, change: (String) -> Unit, label: String, busy: Boolean) {
    OutlinedTextField(
        value, { change(it.take(1024)) }, Modifier.fillMaxWidth(),
        label = { Text(label) }, enabled = !busy, singleLine = true,
        visualTransformation = PasswordVisualTransformation(),
        keyboardOptions = KeyboardOptions(keyboardType = KeyboardType.Password),
    )
}
