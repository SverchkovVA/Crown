package ru.crown.finance.ui

import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import ru.crown.finance.ledger.Bank

@Composable
internal fun BankEditor(banks: List<Bank>, commands: BookCommands, close: () -> Unit) {
    var name by remember { mutableStateOf("") }
    var error by remember { mutableStateOf<String?>(null) }
    LaunchedEffect(name) {
        error = null
        commands.clearError()
    }
    Editor("Банки", commands, error, close, "Создать банк", submit = {
        error = if (name.isBlank()) "Введите название банка" else null
        if (error == null) commands.addBank(name) { name = "" }
    }) {
        Entry(name, { name = it.take(100) }, "Название нового банка", commands.busy)
        if (banks.isEmpty()) Text("Добавьте банки, которыми пользуетесь")
        for (bank in banks) Text(bank.name)
    }
}
