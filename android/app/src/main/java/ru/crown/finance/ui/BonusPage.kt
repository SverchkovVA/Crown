package ru.crown.finance.ui

import androidx.compose.foundation.layout.*
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.material3.*
import androidx.compose.runtime.*
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.testTag
import androidx.compose.ui.semantics.contentDescription
import androidx.compose.ui.semantics.semantics
import androidx.compose.ui.text.input.KeyboardType
import androidx.compose.ui.unit.dp
import ru.crown.finance.ledger.*
import java.math.BigDecimal
import java.math.BigInteger
import java.time.LocalDate
import java.time.YearMonth
import java.util.UUID

internal fun bonusText(units: BigInteger): String = BigDecimal(units, 2).toPlainString().replace('.', ',')
internal fun bonusText(units: Long): String = bonusText(units.toBigInteger())

@Composable
internal fun BonusPage(snapshot: LedgerSnapshot, commands: BookCommands) {
    var selectedId by remember { mutableStateOf<String?>(null) }
    var programEditor by remember { mutableStateOf(false) }
    var entryEditor by remember { mutableStateOf(false) }
    var editingEntry by remember { mutableStateOf<BonusEntryEvent?>(null) }
    var cancelling by remember { mutableStateOf<BonusEntryEvent?>(null) }
    var historyId by remember { mutableStateOf<String?>(null) }
    val programs = snapshot.bonuses.programs
    val program = programs.firstOrNull { it.id == selectedId } ?: programs.firstOrNull { !it.archived } ?: programs.firstOrNull()
    val report = program?.let { snapshot.bonuses.report(it.id, snapshot.month) }
    val rows = snapshot.bonuses.entries.filter {
        it.programId == program?.id && YearMonth.from(LocalDate.parse(it.date)) == snapshot.month
    }.sortedWith(compareByDescending<BonusEntryEvent> { it.date }.thenByDescending { it.sequence })
    Column(Modifier.fillMaxSize()) {
        LazyColumn(Modifier.weight(1f).testTag("bonus-list"), contentPadding = PaddingValues(20.dp), verticalArrangement = Arrangement.spacedBy(12.dp)) {
            item {
                WorkspaceTitle("Бонусы")
                TextButton({ selectedId = null; programEditor = true; commands.clearError() }, enabled = !commands.busy) { Text("Новая программа") }
            }
            item { Text("Баллы программ лояльности учитываются отдельно от денег.") }
            item { Text(monthLabel(snapshot.month), style = MaterialTheme.typography.titleMedium); MonthActions(commands) }
            if (program == null) item { Text("Добавьте программу, чтобы вести начисления и списания бонусов.") }
            else {
                item {
                    Choice("Программа", program.id, programs.map { it.id }, { id ->
                        programs.first { it.id == id }.let { it.name + if (it.archived) " · архив" else "" }
                    }, !commands.busy) { selectedId = it }
                }
                item {
                    Card(Modifier.fillMaxWidth()) {
                        Column(Modifier.padding(16.dp), verticalArrangement = Arrangement.spacedBy(6.dp)) {
                            Text("Остаток на конец месяца", style = MaterialTheme.typography.labelLarge)
                            Text(bonusText(requireNotNull(report).balance) + " баллов", style = MaterialTheme.typography.headlineMedium)
                            Text("Начислено: " + bonusText(report.accrued))
                            Text("Списано: " + bonusText(report.spent) + " · Сгорело: " + bonusText(report.expired))
                            if (report.adjustment != BigInteger.ZERO) Text("Корректировка: " + bonusText(report.adjustment))
                        }
                    }
                }
                item {
                    Text("Начисления по категориям", style = MaterialTheme.typography.titleMedium)
                    if (requireNotNull(report).categories.isEmpty()) Text("В этом месяце начислений нет")
                    for ((id, total) in report.categories.entries.sortedByDescending { it.value }) {
                        Text((snapshot.categories.firstOrNull { it.id == id }?.name ?: "Без категории") + ": " + bonusText(total))
                    }
                }
                item {
                    TextButton({ selectedId = program.id; programEditor = true; commands.clearError() }, enabled = !commands.busy) { Text("Настроить программу") }
                    Text("Записи месяца", style = MaterialTheme.typography.titleMedium)
                    if (rows.isEmpty()) Text("Записей пока нет")
                }
                items(rows, key = BonusEntryEvent::id) { entry ->
                    OutlinedCard(Modifier.fillMaxWidth()) {
                        Column(Modifier.padding(14.dp), verticalArrangement = Arrangement.spacedBy(6.dp)) {
                            Text(entry.bonusKind().label + " · " + bonusText(entry.units) + if (entry.cancelled) " · отменено" else "")
                            Text(LocalDate.parse(entry.date).format(datePattern))
                            if (entry.note.isNotBlank()) Text(entry.note)
                            snapshot.categories.firstOrNull { it.id == entry.categoryId }?.let { Text(it.name) }
                            if (entry.operationId != null) Text("Связано с денежной операцией")
                            TextButton({ historyId = entry.id }) { Text("История записи") }
                            if (!entry.cancelled) {
                                Row {
                                    if (!program.archived) TextButton({ editingEntry = entry; entryEditor = true; commands.clearError() }) { Text("Изменить") }
                                    TextButton({ cancelling = entry; commands.clearError() }) { Text("Отменить запись") }
                                }
                            }
                        }
                    }
                }
            }
        }
        commands.error?.let { FormError(it) }
        if (commands.busy) LinearProgressIndicator(Modifier.fillMaxWidth())
        Button({
            commands.clearError()
            if (program == null || program.archived) { selectedId = program?.id; programEditor = true }
            else { editingEntry = null; entryEditor = true }
        }, Modifier.fillMaxWidth().padding(horizontal = 20.dp, vertical = 8.dp), enabled = !commands.busy) {
            Text(if (program == null) "Новая программа" else if (program.archived) "Настроить программу" else "Добавить бонусы")
        }
    }
    historyId?.let { id ->
        AlertDialog(onDismissRequest = { historyId = null },
            title = { Text("История бонусной записи") },
            text = {
                LazyColumn(verticalArrangement = Arrangement.spacedBy(12.dp)) {
                    items(snapshot.bonuses.entryEvents.filter { it.id == id }) { entry ->
                        Column {
                            Text(entry.bonusKind().label + " · " + bonusText(entry.units) + if (entry.cancelled) " · отменено" else "")
                            Text(LocalDate.parse(entry.date).format(datePattern))
                            if (entry.note.isNotBlank()) Text(entry.note)
                            Text(snapshot.categories.firstOrNull { it.id == entry.categoryId }?.name ?: "Без категории")
                        }
                    }
                }
            }, confirmButton = { TextButton({ historyId = null }) { Text("Готово") } })
    }
    if (programEditor) BonusProgramEditor(program.takeIf { selectedId != null }, commands) { programEditor = false }
    if (entryEditor && program != null) BonusEntryEditor(snapshot, program, editingEntry, commands) { entryEditor = false; editingEntry = null }
    cancelling?.let { entry ->
        AlertDialog(
            onDismissRequest = { if (!commands.busy) cancelling = null },
            title = { Text("Отменить бонусную запись?") },
            text = { Column { Text("Запись останется в истории. Остаток бонусов пересчитается. Денежные операции сохранятся."); commands.error?.let { FormError(it) } } },
            confirmButton = { TextButton({ commands.saveBonusEntry(entry.copy(cancelled = true), entry) { cancelling = null } }, enabled = !commands.busy) { Text("Отменить запись") } },
            dismissButton = { TextButton({ cancelling = null }, enabled = !commands.busy) { Text("Оставить") } },
        )
    }
}

@Composable
private fun BonusProgramEditor(expected: BonusProgramEvent?, commands: BookCommands, close: () -> Unit) {
    var name by remember { mutableStateOf(expected?.name.orEmpty()) }
    var archived by remember { mutableStateOf(expected?.archived ?: false) }
    var error by remember { mutableStateOf<String?>(null) }
    LaunchedEffect(name, archived) { error = null; commands.clearError() }
    Editor(if (expected == null) "Новая программа" else "Программа бонусов", commands, error, close, "Сохранить программу", {
        if (name.isBlank()) error = "Введите название программы"
        else commands.saveBonusProgram(name.trim(), expected, archived, close)
    }) {
        Entry(name, { name = it.take(80) }, "Название программы", commands.busy)
        if (expected != null) Row(Modifier.fillMaxWidth(), horizontalArrangement = Arrangement.SpaceBetween) {
            Text("В архиве", Modifier.weight(1f))
            Switch(archived, { archived = it }, enabled = !commands.busy, modifier = Modifier.semantics { contentDescription = "Программа в архиве" })
        }
        Text("У каждой программы свой остаток. Баллы разных программ не складываются.")
    }
}

@Composable
internal fun BonusEntryEditor(snapshot: LedgerSnapshot, program: BonusProgramEvent, expected: BonusEntryEvent?, commands: BookCommands, close: () -> Unit) {
    var kind by remember { mutableStateOf(expected?.bonusKind() ?: BonusKind.Accrual) }
    var amount by remember { mutableStateOf(expected?.let { bonusText(if (it.bonusKind() == BonusKind.Adjustment) it.units else kotlin.math.abs(it.units)) }.orEmpty()) }
    var date by remember { mutableStateOf(expected?.let { LocalDate.parse(it.date) }?.format(datePattern) ?: snapshot.month.atDay(1).format(datePattern)) }
    var note by remember { mutableStateOf(expected?.note.orEmpty()) }
    var category by remember { mutableStateOf(expected?.categoryId) }
    var operation by remember { mutableStateOf(expected?.operationId) }
    var error by remember { mutableStateOf<String?>(null) }
    val id = remember { expected?.id ?: UUID.randomUUID().toString() }
    LaunchedEffect(kind, amount, date, note, category, operation) { error = null; commands.clearError() }
    Editor(if (expected == null) "Бонусная запись" else "Изменить бонусы", commands, error, close, "Сохранить бонусы", {
        val units = parseMinor(amount, signed = kind == BonusKind.Adjustment)
        val day = dateValue(date)
        when {
            units == null || units == 0L -> error = "Введите ненулевое число, не более двух знаков после запятой"
            day == null -> error = "Введите дату в формате ДД.ММ.ГГГГ"
            else -> commands.saveBonusEntry(BonusEntryEvent(
                id = id, programId = program.id, date = day.toString(), kind = kind.name,
                units = if (kind == BonusKind.Spending || kind == BonusKind.Expiry) -units else units,
                categoryId = category, operationId = operation, note = note.trim(), cancelled = false, recordedAt = 0,
            ), expected, close)
        }
    }) {
        Text(program.name)
        Choice("Вид записи", kind, BonusKind.entries, BonusKind::label, !commands.busy) { kind = it; amount = "" }
        Entry(amount, { amount = it.take(40) }, "Количество бонусов", commands.busy, KeyboardType.Decimal)
        DateEntry(date, { date = it }, "Дата бонусов", commands.busy)
        val categories = snapshot.categories.filter { it.kind == OperationKind.Expense }
        Choice("Категория расхода", category, listOf(null) + categories.map { it.id }, { id ->
            categories.firstOrNull { it.id == id }?.name ?: "Без категории"
        }, !commands.busy) { category = it }
        val operations = snapshot.history.map { it.current.value }.ifEmpty { snapshot.operations }.sortedByDescending { it.date }
        Choice("Связанная операция", operation, listOf(null) + operations.map { it.id }, { id ->
            operations.firstOrNull { it.id == id }?.let { it.date.format(datePattern) + " · " + it.description } ?: "Без связи"
        }, !commands.busy) { operation = it }
        Entry(note, { note = it.take(500) }, "Примечание", commands.busy)
        Text("Рублёвый кешбэк вводится в денежных операциях как поступление.")
    }
}
