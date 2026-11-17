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
import androidx.compose.material3.ModalBottomSheet
import androidx.compose.material3.OutlinedButton
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
import ru.crown.finance.ledger.Operation
import ru.crown.finance.ledger.OperationKind
import ru.crown.finance.ledger.parseMinor
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
    Editor("Новый счёт", commands, error, close) {
        Entry(name, { name = it.take(100) }, "Название счёта", commands.busy)
        Entry(opening, { opening = it.take(24) }, "Начальный остаток, ₽", commands.busy, KeyboardType.Decimal)
        Entry(date, { date = it.take(10) }, "Дата остатка · ДД.ММ.ГГГГ", commands.busy)
        Button(
            onClick = {
                val amount = if (opening.isBlank()) 0L else parseMinor(opening, signed = true)
                val day = dateValue(date)
                val problem = when {
                    name.isBlank() -> "Введите название счёта"
                    amount == null -> "Проверьте начальный остаток"
                    day == null -> "Введите дату в формате ДД.ММ.ГГГГ"
                    else -> null
                }
                error = problem
                if (problem == null && amount != null && day != null) {
                    commands.addAccount(name, amount, day, close)
                }
            },
            enabled = !commands.busy, modifier = Modifier.fillMaxWidth(),
        ) { Text("Сохранить счёт") }
    }
}

@Composable
internal fun OperationEditor(
    accounts: List<AccountBalance>,
    month: YearMonth,
    preferredAccount: String?,
    commands: BookCommands,
    close: () -> Unit,
) {
    val kinds = remember(accounts.size) {
        if (accounts.size >= 2) listOf(OperationKind.Expense, OperationKind.Income, OperationKind.Transfer)
        else listOf(OperationKind.Expense, OperationKind.Income)
    }
    var kind by remember { mutableStateOf(OperationKind.Expense) }
    var origin by remember { mutableStateOf(preferredAccount ?: accounts.first().account.id) }
    var destination by remember { mutableStateOf<String?>(null) }
    var amount by remember { mutableStateOf("") }
    var fee by remember { mutableStateOf("") }
    var date by remember { mutableStateOf(defaultDate(month)) }
    var description by remember { mutableStateOf("") }
    var error by remember { mutableStateOf<String?>(null) }
    val transfer = kind == OperationKind.Transfer
    Editor("Новая операция", commands, error, close) {
        Choice("Вид операции", kind, kinds, OperationKind::label, !commands.busy) { kind = it }
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
        Button(
            onClick = {
                val minor = parseMinor(amount)
                val commission = if (!transfer || fee.isBlank()) 0L else parseMinor(fee)
                val day = dateValue(date)
                val problem = when {
                    minor == null || minor <= 0 -> "Введите положительную сумму"
                    commission == null -> "Проверьте сумму комиссии"
                    transfer && commission > Long.MAX_VALUE - minor -> "Сумма с комиссией слишком велика"
                    day == null -> "Введите дату в формате ДД.ММ.ГГГГ"
                    transfer && destination == null -> "Выберите счёт зачисления"
                    else -> null
                }
                error = problem
                if (problem == null && minor != null && commission != null && day != null) {
                    commands.addOperation(
                        Operation(
                            UUID.randomUUID().toString(), kind, day, origin,
                            minor, description.trim().ifBlank { kind.label() },
                            destination.takeIf { transfer }, commission,
                        ),
                        close,
                    )
                }
            },
            enabled = !commands.busy, modifier = Modifier.fillMaxWidth(),
        ) { Text("Сохранить операцию") }
    }
}

@OptIn(ExperimentalMaterial3Api::class)
@Composable
private fun Editor(
    title: String,
    commands: BookCommands,
    error: String?,
    close: () -> Unit,
    content: @Composable () -> Unit,
) {
    ModalBottomSheet(onDismissRequest = { if (!commands.busy) close() }) {
        Column(
            Modifier.fillMaxWidth().imePadding().verticalScroll(rememberScrollState())
                .padding(start = 24.dp, end = 24.dp, bottom = 24.dp),
            verticalArrangement = Arrangement.spacedBy(10.dp),
        ) {
            WorkspaceTitle(title)
            content()
            (error ?: commands.error)?.let { FormError(it) }
            if (commands.busy) LinearProgressIndicator(Modifier.fillMaxWidth())
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

private fun defaultDate(month: YearMonth): String {
    val today = LocalDate.now()
    return (if (month == YearMonth.from(today)) today else month.atEndOfMonth()).format(datePattern)
}
