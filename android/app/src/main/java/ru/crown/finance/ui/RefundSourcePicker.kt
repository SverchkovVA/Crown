package ru.crown.finance.ui

import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import ru.crown.finance.ledger.*

@Composable
internal fun RefundSourcePicker(
    row: StatementRow,
    snapshot: LedgerSnapshot,
    commands: BookCommands,
    close: () -> Unit,
    choose: (Operation) -> Unit,
) {
    val purchases = remember(row, snapshot) {
        snapshot.history.mapNotNull { it.current.takeUnless(Revision::cancelled)?.value }
            .filter { it.kind == OperationKind.Expense && it.date <= row.date && availableRefund(it, snapshot).isNotEmpty() }
            .sortedByDescending(Operation::date)
    }
    var selected by remember { mutableStateOf<String?>(null) }
    var error by remember { mutableStateOf<String?>(null) }
    Editor("Исходная покупка", commands, error, close, "Продолжить", submit = {
        val value = purchases.firstOrNull { it.id == selected }
        if (value == null) error = "Выберите покупку" else choose(value)
    }) {
        Text(row.description)
        Text("Возврат в выписке · ${money(row.signedMinor)}")
        if (purchases.isEmpty()) {
            Text("Сначала запишите исходную покупку в книгу")
        } else {
            Choice("Покупка", selected, purchases.map(Operation::id), { id ->
                val purchase = purchases.first { it.id == id }
                "${purchase.date.format(datePattern)} · ${purchase.description} · ${money(purchase.amountMinor)}"
            }, !commands.busy) { selected = it; error = null }
        }
    }
}
