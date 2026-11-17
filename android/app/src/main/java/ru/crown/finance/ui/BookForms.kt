package ru.crown.finance.ui

import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.imePadding
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.verticalScroll
import androidx.compose.material3.Button
import androidx.compose.material3.DropdownMenu
import androidx.compose.material3.DropdownMenuItem
import androidx.compose.material3.ExperimentalMaterial3Api
import androidx.compose.material3.LinearProgressIndicator
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.ModalBottomSheet
import androidx.compose.material3.OutlinedButton
import androidx.compose.material3.rememberModalBottomSheetState
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Modifier
import androidx.compose.ui.text.input.KeyboardType
import androidx.compose.ui.unit.dp
import ru.crown.finance.ledger.AccountBalance
import ru.crown.finance.ledger.Category
import ru.crown.finance.ledger.categoryKinds
import ru.crown.finance.ledger.Operation
import ru.crown.finance.ledger.OperationKind
import ru.crown.finance.ledger.parseMinor
import java.math.BigDecimal
import java.time.LocalDate
import java.time.YearMonth
import java.time.format.DateTimeFormatter
import java.time.format.ResolverStyle
import java.util.UUID

internal data class BookCommands(
    val busy: Boolean,
    val error: String?,
    val clearError: () -> Unit,
    val changeMonth: (Int) -> Unit,
    val addAccount: (String, Long, LocalDate, () -> Unit) -> Unit,
    val addOperation: (Operation, () -> Unit) -> Unit,
    val reviseOperation: (Operation, Operation?, () -> Unit) -> Unit,
    val backup: (CharArray) -> Unit,
    val addCategory: (String, OperationKind, () -> Unit) -> Unit,
)

private val datePattern = DateTimeFormatter.ofPattern("dd.MM.uuuu").withResolverStyle(ResolverStyle.STRICT)

private fun dateValue(text: String): LocalDate? =
    runCatching { LocalDate.parse(text.trim(), datePattern) }.getOrNull()?.takeIf { it.year in 1900..9999 }

@Composable
internal fun AccountEditor(month: YearMonth, commands: BookCommands, close: () -> Unit) {
    var name by remember { mutableStateOf("") }
    var opening by remember { mutableStateOf("") }
    var date by remember { mutableStateOf(defaultDate(month)) }
    var error by remember { mutableStateOf<String?>(null) }
    Editor(
        "Новый счёт", commands, error, close, "Сохранить счёт",
        submit = {
            val amount = if (opening.isBlank()) 0L else parseMinor(opening, signed = true)
            val day = dateValue(date)
            error = when {
                name.isBlank() -> "Введите название счёта"
                amount == null -> "Проверьте начальный остаток"
                day == null -> "Введите дату в формате ДД.ММ.ГГГГ"
                else -> null
            }
            if (error == null && amount != null && day != null) {
                commands.addAccount(name, amount, day, close)
            }
        },
    ) {
        Entry(name, { name = it.take(100) }, "Название счёта", commands.busy)
        Entry(opening, { opening = it.take(24) }, "Начальный остаток, ₽", commands.busy, KeyboardType.Decimal)
        Entry(date, { date = it.take(10) }, "Дата остатка · ДД.ММ.ГГГГ", commands.busy)
    }
}

@Composable
internal fun OperationEditor(
    accounts: List<AccountBalance>,
    categories: List<Category>,
    month: YearMonth,
    preferredAccount: String?,
    commands: BookCommands,
    close: () -> Unit,
    existing: Operation? = null,
) {
    val kinds = remember(accounts.size) {
        if (accounts.size >= 2) listOf(OperationKind.Expense, OperationKind.Income, OperationKind.Transfer)
        else listOf(OperationKind.Expense, OperationKind.Income)
    }
    var kind by remember { mutableStateOf(existing?.kind ?: OperationKind.Expense) }
    var origin by remember { mutableStateOf(existing?.accountId ?: preferredAccount ?: accounts.first().account.id) }
    var destination by remember { mutableStateOf(existing?.destinationId) }
    var amount by remember { mutableStateOf(existing?.let { amountText(it.amountMinor) }.orEmpty()) }
    var fee by remember { mutableStateOf(existing?.feeMinor?.takeIf { it > 0 }?.let(::amountText).orEmpty()) }
    var category by remember { mutableStateOf(existing?.categoryId) }
    var date by remember { mutableStateOf(existing?.date?.format(datePattern) ?: defaultDate(month)) }
    var description by remember { mutableStateOf(existing?.description.orEmpty()) }
    var error by remember { mutableStateOf<String?>(null) }
    val transfer = kind == OperationKind.Transfer
    val availableCategories = categories.filter { it.kind == kind }
    Editor(
        if (existing == null) "Новая операция" else "Исправление операции",
        commands, error, close, "Сохранить операцию",
        submit = {
            val minor = parseMinor(amount)
            val commission = if (!transfer || fee.isBlank()) 0L else parseMinor(fee)
            val day = dateValue(date)
            error = when {
                minor == null || minor <= 0 -> "Введите положительную сумму"
                commission == null -> "Проверьте сумму комиссии"
                transfer && commission > Long.MAX_VALUE - minor -> "Сумма с комиссией слишком велика"
                day == null -> "Введите дату в формате ДД.ММ.ГГГГ"
                transfer && destination == null -> "Выберите счёт зачисления"
                else -> null
            }
            if (error == null && minor != null && commission != null && day != null) {
                val operation = Operation(
                    existing?.id ?: UUID.randomUUID().toString(), kind, day, origin,
                    minor, description.trim().ifBlank { kind.label() },
                    destination.takeIf { transfer }, commission, category,
                )
                if (existing == null) {
                    commands.addOperation(operation, close)
                } else {
                    commands.reviseOperation(existing, operation, close)
                }
            }
        },
    ) {
        Choice("Вид операции", kind, kinds, OperationKind::label, !commands.busy) {
            if (kind != it) category = null
            kind = it
        }
        Choice(
            if (transfer) "Со счёта" else "Счёт", origin,
            accounts.map { it.account.id }, { id -> accounts.first { it.account.id == id }.account.name },
            !commands.busy,
        ) {
            origin = it
            if (destination == it) destination = null
        }
        if (transfer) {
            Choice(
                "На счёт", destination, accounts.map { it.account.id }.filter { it != origin },
                { id -> accounts.first { it.account.id == id }.account.name }, !commands.busy,
            ) { destination = it }
        }
        Entry(amount, { amount = it.take(24) }, "Сумма, ₽", commands.busy, KeyboardType.Decimal)
        if (transfer) Entry(fee, { fee = it.take(24) }, "Комиссия, ₽", commands.busy, KeyboardType.Decimal)
        Entry(date, { date = it.take(10) }, "Дата · ДД.ММ.ГГГГ", commands.busy)
        Entry(description, { description = it.take(200) }, "Описание", commands.busy)
        if (availableCategories.isNotEmpty()) {
            val names = availableCategories.associate { it.id to it.name }
            Choice("Категория", category, listOf(null) + names.keys, { names[it] ?: "Без категории" }, !commands.busy) {
                category = it
            }
        }
    }
}

@Composable
internal fun CategoryEditor(categories: List<Category>, commands: BookCommands, close: () -> Unit) {
    var name by remember { mutableStateOf("") }
    var kind by remember { mutableStateOf(OperationKind.Expense) }
    var error by remember { mutableStateOf<String?>(null) }
    Editor(
        "Категории", commands, error, close, "Создать категорию",
        submit = {
            error = if (name.isBlank()) "Введите название категории" else null
            if (error == null) commands.addCategory(name, kind) { name = "" }
        },
    ) {
        Entry(name, { name = it.take(80) }, "Название новой категории", commands.busy)
        Choice("Вид категории", kind, categoryKinds, OperationKind::label, !commands.busy) { kind = it }
        if (categories.isNotEmpty()) {
            for (type in categoryKinds) {
                val names = categories.filter { it.kind == type }
                if (names.isNotEmpty()) {
                    Text(type.label(), style = MaterialTheme.typography.titleMedium)
                    for (entry in names) Text(entry.name)
                }
            }
        }
    }
}

@OptIn(ExperimentalMaterial3Api::class)
@Composable
private fun Editor(
    title: String,
    commands: BookCommands,
    error: String?,
    close: () -> Unit,
    submitLabel: String,
    submit: () -> Unit,
    content: @Composable () -> Unit,
) {
    ModalBottomSheet(
        onDismissRequest = { if (!commands.busy) close() },
        sheetState = rememberModalBottomSheetState(skipPartiallyExpanded = true),
    ) {
        Column(
            Modifier.fillMaxWidth().imePadding().padding(start = 24.dp, end = 24.dp, bottom = 16.dp),
            verticalArrangement = Arrangement.spacedBy(10.dp),
        ) {
            WorkspaceTitle(title)
            Column(
                Modifier.weight(1f, fill = false).verticalScroll(rememberScrollState()),
                verticalArrangement = Arrangement.spacedBy(10.dp),
            ) { content() }
            (error ?: commands.error)?.let { FormError(it) }
            if (commands.busy) LinearProgressIndicator(Modifier.fillMaxWidth())
            Button(submit, Modifier.fillMaxWidth(), enabled = !commands.busy) { Text(submitLabel) }
        }
    }
}

@Composable
private fun <T> Choice(
    title: String,
    selected: T?,
    values: List<T>,
    describe: (T) -> String,
    enabled: Boolean,
    choose: (T) -> Unit,
) {
    var expanded by remember { mutableStateOf(false) }
    Box(Modifier.fillMaxWidth()) {
        val label = selected?.let(describe)
        OutlinedButton(
            { expanded = true }, Modifier.fillMaxWidth(), enabled = enabled,
        ) { Text(if (label == null) "Выбрать: ${title.lowercase()}" else "$title: $label") }
        DropdownMenu(expanded, { expanded = false }) {
            for (item in values) {
                DropdownMenuItem(text = { Text(describe(item)) }, onClick = {
                    expanded = false
                    choose(item)
                })
            }
        }
    }
}

private fun amountText(minor: Long): String = BigDecimal.valueOf(minor, 2).toPlainString()

private fun defaultDate(month: YearMonth): String {
    val today = LocalDate.now()
    return (if (month == YearMonth.from(today)) today else month.atEndOfMonth()).format(datePattern)
}
