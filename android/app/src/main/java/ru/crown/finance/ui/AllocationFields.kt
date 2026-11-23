package ru.crown.finance.ui

import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Modifier
import androidx.compose.ui.text.input.KeyboardType
import androidx.compose.ui.unit.dp
import ru.crown.finance.ledger.CatalogType
import ru.crown.finance.ledger.catalogArchived
import ru.crown.finance.ledger.Allocation
import ru.crown.finance.ledger.Category
import ru.crown.finance.ledger.LedgerSnapshot
import ru.crown.finance.ledger.Operation
import ru.crown.finance.ledger.OperationKind
import ru.crown.finance.ledger.allocations
import ru.crown.finance.ledger.fingerprint
import ru.crown.finance.ledger.parseMinor
import java.math.BigInteger
import java.util.UUID

internal data class DraftAllocation(val categoryId: String?, val amount: String)

@Composable
internal fun AllocationFields(
    rows: List<DraftAllocation>,
    categories: List<Category>,
    busy: Boolean,
    change: (List<DraftAllocation>) -> Unit,
) {
    for ((index, row) in rows.withIndex()) {
        Column(verticalArrangement = Arrangement.spacedBy(4.dp)) {
            Text("Часть ${index + 1}", style = MaterialTheme.typography.labelLarge)
            Choice(
                "Категория части ${index + 1}", row.categoryId,
                listOf(null) + categories.map(Category::id),
                { id -> categories.firstOrNull { it.id == id }?.name ?: "Без категории" },
                !busy,
            ) { id -> change(rows.mapIndexed { i, part -> if (i == index) part.copy(categoryId = id) else part }) }
            Entry(
                row.amount,
                { text -> change(rows.mapIndexed { i, part -> if (i == index) part.copy(amount = text.take(24)) else part }) },
                "Сумма части ${index + 1}, ₽", busy, KeyboardType.Decimal,
            )
            if (rows.size > 2) TextButton(
                { change(rows.filterIndexed { i, _ -> i != index }) }, enabled = !busy,
            ) { Text("Убрать часть ${index + 1}") }
        }
    }
    if (rows.size < 100) TextButton(
        { change(rows + DraftAllocation(null, "")) }, enabled = !busy,
    ) { Text("Добавить часть") }
}

internal fun availableRefund(
    purchase: Operation,
    snapshot: LedgerSnapshot,
    exceptId: String? = null,
): List<Allocation> {
    val spent = snapshot.history.mapNotNull { it.current.takeUnless { row -> row.cancelled }?.value }
        .filter { it.kind == OperationKind.Refund && it.refundOf == purchase.id && it.id != exceptId }
        .flatMap(Operation::allocations).groupBy(Allocation::categoryId)
        .mapValues { (_, values) -> values.fold(BigInteger.ZERO) { sum, part -> sum + part.amountMinor.toBigInteger() } }
    return purchase.allocations().mapNotNull { part ->
        val remaining = part.amountMinor.toBigInteger() - spent.getOrDefault(part.categoryId, BigInteger.ZERO)
        remaining.takeIf { it.signum() > 0 }?.let { Allocation(part.categoryId, it.toString().toLong()) }
    }
}

@Composable
internal fun RefundEditor(
    purchase: Operation,
    snapshot: LedgerSnapshot,
    commands: BookCommands,
    close: () -> Unit,
    existing: Operation? = null,
    suggestedAmount: Long? = null,
    suggestedDate: java.time.LocalDate? = null,
) {
    val available = remember(purchase, snapshot, existing) { availableRefund(purchase, snapshot, existing?.id) }
    val initial = existing?.allocations()?.associate { it.categoryId to it.amountMinor }
        ?: suggestedAmount?.let { amount ->
            if (available.size == 1) mapOf(available.single().categoryId to amount) else emptyMap()
        }
    var rows by remember {
        mutableStateOf(available.map { part ->
            DraftAllocation(part.categoryId, amountText(initial?.get(part.categoryId) ?: if (suggestedAmount != null) 0L else part.amountMinor))
        })
    }
    var account by remember { mutableStateOf(existing?.accountId ?: purchase.accountId) }
    var date by remember { mutableStateOf(existing?.date?.format(datePattern) ?: suggestedDate?.format(datePattern) ?: defaultDate(snapshot.month)) }
    var description by remember { mutableStateOf(existing?.description ?: "Возврат · ${purchase.description}") }
    var error by remember { mutableStateOf<String?>(null) }
    LaunchedEffect(rows, account, date, description) {
        error = null
        commands.clearError()
    }
    Editor("Возврат покупки", commands, error, close, "Сохранить возврат", submit = {
        val values = rows.map { parseMinor(it.amount) }
        val selected = rows.zip(values).mapNotNull { (row, amount) ->
            amount?.takeIf { it > 0 }?.let { Allocation(row.categoryId, it) }
        }
        val total = selected.fold(BigInteger.ZERO) { sum, part -> sum + part.amountMinor.toBigInteger() }
        val day = dateValue(date)
        error = when {
            values.any { it == null } -> "Проверьте суммы возврата"
            total.signum() <= 0 -> "Введите сумму хотя бы одной части возврата"
            total > Long.MAX_VALUE.toBigInteger() -> "Сумма возврата слишком велика"
            selected.any { item -> item.amountMinor > available.first { it.categoryId == item.categoryId }.amountMinor } ->
                "Сумма возврата превышает доступный остаток"
            day == null -> "Введите дату в формате ДД.ММ.ГГГГ"
            day < purchase.date -> "Дата возврата предшествует покупке"
            else -> null
        }
        if (error == null && day != null) {
            val value = Operation(
                existing?.id ?: UUID.randomUUID().toString(), OperationKind.Refund, day,
                account, total.toString().toLong(), description.trim().ifBlank { "Возврат покупки" },
                categoryId = selected.singleOrNull()?.categoryId,
                parts = selected.takeIf { it.size > 1 }.orEmpty(),
                refundOf = purchase.id, refundVersion = purchase.fingerprint(),
            )
            if (existing == null) commands.addOperation(value, close) else commands.reviseOperation(existing, value, close)
        }
    }) {
        Text(purchase.description, style = MaterialTheme.typography.titleMedium)
        Text("Возврат уменьшает расходы на дату получения.")
        Choice("Счёт зачисления", account, snapshot.balances.filter { !snapshot.catalogArchived(CatalogType.Account, it.account.id) || it.account.id == purchase.accountId || it.account.id == existing?.accountId }.map { it.account.id }, { id ->
            snapshot.balances.first { it.account.id == id }.account.name
        }, !commands.busy) { account = it }
        for ((index, part) in rows.withIndex()) {
            val name = snapshot.categories.firstOrNull { it.id == part.categoryId }?.name ?: "Без категории"
            val limit = available[index].amountMinor
            Text("$name · доступно ${money(limit)}", style = MaterialTheme.typography.bodyMedium)
            Entry(
                part.amount, { text -> rows = rows.mapIndexed { i, row ->
                    if (i == index) row.copy(amount = text.take(24)) else row
                } }, "Возврат части ${index + 1}, ₽", commands.busy, KeyboardType.Decimal,
            )
        }
        DateEntry(date, { date = it }, "Дата · ДД.ММ.ГГГГ", commands.busy)
        Entry(description, { description = it.take(200) }, "Описание", commands.busy)
    }
}
