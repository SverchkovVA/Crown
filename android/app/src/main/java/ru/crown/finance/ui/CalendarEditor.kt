package ru.crown.finance.ui

import androidx.compose.foundation.layout.*
import androidx.compose.material3.*
import androidx.compose.runtime.*
import androidx.compose.ui.Modifier
import androidx.compose.ui.semantics.contentDescription
import androidx.compose.ui.semantics.semantics
import androidx.compose.ui.text.input.KeyboardType
import androidx.compose.ui.unit.dp
import ru.crown.finance.ledger.*
import java.time.LocalDate
import java.util.UUID

private data class PaymentDraft(
    val name: String = "", val amount: String = "", val income: Boolean = false,
    val first: String = LocalDate.now().format(datePattern), val unit: RepeatUnit = RepeatUnit.Once,
    val interval: String = "1", val last: String = "", val hasLast: Boolean = false,
) {
    fun value() = PaymentSchedule(name.trim(), requireNotNull(parseMinor(amount)) { "Проверьте сумму платежа" }, income,
        requireNotNull(dateValue(first)) { "Проверьте первую дату платежа" }, unit,
        if (unit == RepeatUnit.Once) 1 else requireNotNull(interval.toIntOrNull()) { "Введите целый интервал повтора" },
        if (unit != RepeatUnit.Once && hasLast) requireNotNull(dateValue(last)) { "Проверьте последнюю дату платежа" } else null
    ).also { it.validate() }
}
@Composable
internal fun CalendarEditor(expected: PlanningEvent?, initial: CalendarPlan?, title: String, commands: BookCommands, close: () -> Unit, saved: (String) -> Unit) {
    val id = remember { expected?.id ?: UUID.randomUUID().toString() }
    var name by remember { mutableStateOf(title) }
    var rows by remember { mutableStateOf(initial?.schedules?.map {
        PaymentDraft(it.name, amountText(it.amount), it.income, it.first.format(datePattern), it.unit, it.interval.toString(), it.last?.format(datePattern).orEmpty(), it.last != null)
    } ?: listOf(PaymentDraft())) }
    var expanded by remember { mutableStateOf<Int?>(if (initial == null) 0 else null) }
    var archived by remember { mutableStateOf(expected?.archived ?: false) }
    var error by remember { mutableStateOf<String?>(null) }
    LaunchedEffect(name, rows, archived) { error = null; commands.clearError() }
    Editor(if (expected == null) "Новый календарь" else "Условия календаря", commands, error, close, "Сохранить календарь", {
        try {
            require(name.isNotBlank()) { "Введите название календаря" }
            val plan = CalendarPlan(rows.mapIndexed { index, draft ->
                try { draft.value() } catch (failure: IllegalArgumentException) {
                    expanded = index
                    throw IllegalArgumentException("Платёж " + (index + 1) + ": " + failure.message)
                }
            }).also { it.validate() }
            commands.savePlan(id, name.trim(), plan, expected, archived) { saved(id) }
        } catch (failure: IllegalArgumentException) { error = failure.message ?: "Проверьте условия календаря" }
    }) {
        Entry(name, { name = it.take(80) }, "Название календаря", commands.busy)
        rows.forEachIndexed { index, draft ->
            fun update(value: PaymentDraft) { rows = rows.toMutableList().also { it[index] = value } }
            OutlinedCard(Modifier.fillMaxWidth()) {
                Column(Modifier.padding(12.dp)) {
                    TextButton({ expanded = if (expanded == index) null else index }) { Text("Платёж " + (index + 1) + " · " + draft.name.ifBlank { "Укажите условия" }) }
                    if (expanded == index) {
                        Entry(draft.name, { update(draft.copy(name = it.take(100))) }, "Название платежа", commands.busy)
                        Entry(draft.amount, { update(draft.copy(amount = it.take(24))) }, "Сумма платежа, ₽", commands.busy, KeyboardType.Decimal)
                        Choice("Движение", draft.income, listOf(false, true), { if (it) "Поступление" else "Списание" }, !commands.busy) { update(draft.copy(income = it)) }
                        DateEntry(draft.first, { update(draft.copy(first = it)) }, "Первый платёж", commands.busy)
                        Choice("Повтор", draft.unit, RepeatUnit.entries, RepeatUnit::label, !commands.busy) { update(draft.copy(unit = it)) }
                        if (draft.unit != RepeatUnit.Once) {
                            Entry(draft.interval, { update(draft.copy(interval = it.take(4))) }, "Интервал повтора", commands.busy, KeyboardType.Number)
                            CalendarSwitch("Есть последняя дата", draft.hasLast, commands.busy) { update(draft.copy(hasLast = it)) }
                            if (draft.hasLast) DateEntry(draft.last, { update(draft.copy(last = it)) }, "Последняя дата", commands.busy)
                        }
                        TextButton({ rows = rows.filterIndexed { position, _ -> position != index }; expanded = null }, enabled = !commands.busy) { Text("Убрать платёж") }
                    } else Text((if (draft.income) "+" else "−") + draft.amount + " ₽ · " + draft.unit.label)
                }
            }
        }
        TextButton({ rows = rows + PaymentDraft(); expanded = rows.lastIndex }, enabled = !commands.busy && rows.size < 1000) { Text("Добавить платёж") }
        if (expected != null) CalendarSwitch("Календарь в архиве", archived, commands.busy) { archived = it }
        Text("Сохранение календаря не проводит платежи и не меняет остатки.")
    }
}
@Composable
private fun CalendarSwitch(label: String, checked: Boolean, busy: Boolean, change: (Boolean) -> Unit) {
    Row(Modifier.fillMaxWidth(), horizontalArrangement = Arrangement.SpaceBetween) {
        Text(label, Modifier.weight(1f))
        Switch(checked, change, enabled = !busy, modifier = Modifier.semantics { contentDescription = label })
    }
}
