package ru.crown.finance.ui

import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.verticalScroll
import androidx.compose.material3.Button
import androidx.compose.material3.ExperimentalMaterial3Api
import androidx.compose.material3.LinearProgressIndicator
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Icon
import androidx.compose.material3.OutlinedButton
import androidx.compose.material3.ModalBottomSheet
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Modifier
import androidx.compose.ui.res.painterResource
import ru.crown.finance.R
import androidx.compose.ui.unit.dp

@Composable
internal fun ProtectionPage(
    startBackup: () -> Unit,
    openHistory: () -> Unit,
    openCategories: () -> Unit,
    inspectBackup: () -> Unit,
) {
    Column(
        Modifier.fillMaxWidth().verticalScroll(rememberScrollState()).padding(20.dp),
        verticalArrangement = Arrangement.spacedBy(12.dp),
    ) {
        WorkspaceTitle("Настройки")
        OutlinedButton(openCategories) { Text("Категории") }
        OutlinedButton(openHistory) {
            Icon(painterResource(R.drawable.ic_history), contentDescription = null, modifier = Modifier.padding(end = 8.dp))
            Text("История изменений")
        }
        Text("Защита и копии", style = MaterialTheme.typography.titleLarge)
        Text("Зашифрованную копию можно перенести на другой телефон и открыть с паролем копии.")
        Button(startBackup) { Text("Создать резервную копию") }
        OutlinedButton(inspectBackup) { Text("Проверить резервную копию") }
    }
}

@OptIn(ExperimentalMaterial3Api::class)
@Composable
internal fun BackupForm(commands: BookCommands, close: () -> Unit) {
    var password by remember { mutableStateOf("") }
    var confirmation by remember { mutableStateOf("") }
    var mismatch by remember { mutableStateOf(false) }
    ModalBottomSheet(onDismissRequest = { if (!commands.busy) close() }) {
        Column(
            Modifier.fillMaxWidth().verticalScroll(rememberScrollState())
                .padding(start = 24.dp, end = 24.dp, bottom = 24.dp),
            verticalArrangement = Arrangement.spacedBy(8.dp),
        ) {
            WorkspaceTitle("Резервная копия")
            Text("Задайте отдельную парольную фразу для копии. Она понадобится при восстановлении.")
            SecretField(password, { password = it }, "Пароль копии", commands.busy)
            SecretField(confirmation, { confirmation = it }, "Повторите пароль копии", commands.busy)
            if (mismatch && password != confirmation) FormError("Пароли копии различаются")
            commands.error?.let { FormError(it) }
            if (commands.busy) LinearProgressIndicator(Modifier.fillMaxWidth())
            Button(
                onClick = {
                    if (password != confirmation) {
                        mismatch = true
                    } else {
                        val secret = password.toCharArray()
                        password = ""
                        confirmation = ""
                        commands.backup(secret)
                    }
                },
                enabled = !commands.busy && password.isNotEmpty() && confirmation.isNotEmpty(),
                modifier = Modifier.fillMaxWidth(),
            ) { Text("Выбрать файл для копии") }
        }
    }
}
