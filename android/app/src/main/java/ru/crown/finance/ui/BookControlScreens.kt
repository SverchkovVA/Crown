package ru.crown.finance.ui

import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.PaddingValues
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.material3.AlertDialog
import androidx.compose.material3.Button
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Modifier
import androidx.compose.ui.text.input.KeyboardType
import androidx.compose.ui.unit.dp
import ru.crown.finance.ledger.*
import java.math.BigInteger

@Composable
internal fun PeriodsPage(snapshot: LedgerSnapshot, commands: BookCommands) {
    val closed = snapshot.month in closedMonths(snapshot.periods)
    var reason by remember(snapshot.month, closed) { mutableStateOf("") }
    var confirm by remember { mutableStateOf(false) }
    LazyColumn(
        Modifier.fillMaxSize(), contentPadding = PaddingValues(20.dp),
        verticalArrangement = Arrangement.spacedBy(10.dp),
    ) {
        item {
            WorkspaceTitle("Периоды")
            Text(monthLabel(snapshot.month), style = MaterialTheme.typography.titleLarge)
            MonthActions(commands)
            Text(if (closed) "Месяц закрыт" else "Месяц открыт", style = MaterialTheme.typography.titleMedium)
            if (closed) Entry(reason, { reason = it.take(200) }, "Причина открытия месяца", commands.busy)
            Button(
                onClick = { confirm = true }, enabled = !commands.busy && (!closed || reason.isNotBlank()),
                modifier = Modifier.fillMaxWidth(),
            ) { Text(if (closed) "Открыть месяц" else "Закрыть месяц") }
            commands.error?.let { FormError(it) }
        }
        items(snapshot.periods.filter { it.month == snapshot.month.toString() }.asReversed()) { event ->
            Column(Modifier.fillMaxWidth().padding(vertical = 4.dp)) {
                Text(if (event.closed) "Закрытие месяца" else "Открытие месяца", style = MaterialTheme.typography.titleSmall)
                if (event.reason.isNotBlank()) Text(event.reason)
                Text(recordedTime(event.recordedAt), style = MaterialTheme.typography.bodySmall)
            }
        }
    }
    if (confirm) AlertDialog(
        onDismissRequest = { confirm = false },
        title = { Text(if (closed) "Открыть месяц?" else "Закрыть месяц?") },
        text = {
            Text(if (closed) "В этом месяце снова можно будет изменять операции."
                else "Добавление, исправление и отмена операций за этот месяц будут заблокированы.")
        },
        confirmButton = {
            TextButton(onClick = {
                confirm = false
                commands.setPeriodClosed(snapshot.month, !closed, reason)
            }) { Text("Подтвердить") }
        },
        dismissButton = { TextButton(onClick = { confirm = false }) { Text("Назад") } },
    )
}

@Composable
internal fun ReconciliationEditor(snapshot: LedgerSnapshot, commands: BookCommands, close: () -> Unit) {
    var account by remember { mutableStateOf(snapshot.balances.first().account.id) }
    var date by remember { mutableStateOf(defaultDate(snapshot.month)) }
    var actual by remember { mutableStateOf("") }
    var error by remember { mutableStateOf<String?>(null) }
    val day = dateValue(date)
    val preview = remember(snapshot, account, day) {
        day?.let { previewReconciliation(snapshot.history, snapshot.balances.map { it.account }, account, it) }
    }
    val amount = parseMinor(actual, signed = true)
    Editor(
        "Сверка остатка", commands, error, close, "Сохранить сверку",
        submit = {
            error = when {
                preview == null -> "Введите дату в формате ДД.ММ.ГГГГ"
                amount == null -> "Введите остаток из банка"
                else -> null
            }
            if (preview != null && amount != null) commands.reconcile(preview, amount, close)
        },
    ) {
        Choice("Счёт", account, snapshot.balances.map { it.account.id }, { id ->
            snapshot.balances.first { it.account.id == id }.account.name
        }, !commands.busy) { account = it }
        Entry(date, { date = it.take(10) }, "Дата сверки · ДД.ММ.ГГГГ", commands.busy)
        Entry(actual, { actual = it.take(24) }, "Остаток в банке, ₽", commands.busy, KeyboardType.Decimal)
        if (preview != null) {
            Text("В книге · ${money(preview.calculatedMinor)}", style = MaterialTheme.typography.titleMedium)
            if (amount != null) {
                val difference = amount.toBigInteger() - preview.calculatedMinor
                Text(if (difference == BigInteger.ZERO) "Остатки совпадают" else "Расхождение · ${money(difference, signed = true)}")
            }
        }
        Text("Сверка сохраняет результат сравнения. Остаток в книге рассчитывается по операциям.")
    }
}

@Composable
internal fun ReconciliationsPage(snapshot: LedgerSnapshot, commands: BookCommands, add: () -> Unit) {
    val accounts = snapshot.balances.map { it.account }
    LazyColumn(
        Modifier.fillMaxSize(), contentPadding = PaddingValues(20.dp),
        verticalArrangement = Arrangement.spacedBy(12.dp),
    ) {
        item {
            WorkspaceTitle("Сверка остатков")
            Button(add, enabled = !commands.busy && accounts.isNotEmpty()) { Text("Новая сверка") }
            if (snapshot.reconciliations.isEmpty()) Text("Сохранённых сверок пока нет")
        }
        items(snapshot.reconciliations.asReversed(), key = Reconciliation::id) { item ->
            val date = java.time.LocalDate.parse(item.date)
            val current = previewReconciliation(snapshot.history, accounts, item.accountId, date)
            Column(Modifier.fillMaxWidth(), verticalArrangement = Arrangement.spacedBy(2.dp)) {
                Text(accounts.first { it.id == item.accountId }.name, style = MaterialTheme.typography.titleMedium)
                Text(date.format(datePattern))
                Text("В банке · ${money(item.actualMinor)}")
                Text("В книге при сверке · ${money(item.calculatedMinor.toBigInteger())}")
                Text(if (item.difference.signum() == 0) "Остатки совпали" else "Расхождение · ${money(item.difference, signed = true)}")
                if (current.fingerprint != item.fingerprint) {
                    Text("Данные изменились после сверки", color = MaterialTheme.colorScheme.error)
                }
            }
        }
    }
}

private fun recordedTime(millis: Long): String =
    java.time.format.DateTimeFormatter.ofPattern("dd.MM.uuuu HH:mm")
        .withZone(java.time.ZoneId.systemDefault()).format(java.time.Instant.ofEpochMilli(millis))
