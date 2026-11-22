package ru.crown.finance.ui

import androidx.compose.material3.Text
import androidx.compose.runtime.*

@Composable
internal fun PasswordForm(commands: BookCommands, close: () -> Unit) {
    var credential by remember { mutableStateOf("") }
    var password by remember { mutableStateOf("") }
    var confirmation by remember { mutableStateOf("") }
    var error by remember { mutableStateOf<String?>(null) }
    LaunchedEffect(credential, password, confirmation) { error = null; commands.clearError() }
    Editor("Смена пароля", commands, error, close, "Изменить пароль", {
        when {
            credential.isEmpty() -> error = "Введите текущий пароль или код CA1"
            password.isEmpty() -> error = "Введите новую парольную фразу"
            password != confirmation -> error = "Новые парольные фразы различаются"
            else -> {
                val current = credential.toCharArray()
                val next = password.toCharArray()
                credential = ""; password = ""; confirmation = ""
                commands.changePassword(current, next, close)
            }
        }
    }) {
        Text("Введите текущий пароль или CA1, затем новую парольную фразу.")
        SecretField(credential, { credential = it }, "Пароль или CA1", commands.busy)
        SecretField(password, { password = it }, "Новый пароль", commands.busy)
        SecretField(confirmation, { confirmation = it }, "Повторите пароль", commands.busy)
        Text("Сохранённый CA1 продолжит работать. Пароль резервных копий задаётся отдельно.")
    }
}
