package ru.crown.finance.ui

import androidx.activity.compose.BackHandler
import androidx.compose.foundation.layout.*
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.material3.*
import androidx.compose.runtime.*
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.testTag
import androidx.compose.ui.res.painterResource
import androidx.compose.ui.semantics.semantics
import androidx.compose.ui.semantics.stateDescription
import androidx.compose.ui.text.input.KeyboardType
import androidx.compose.ui.unit.dp
import kotlinx.coroutines.CancellationException
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.ensureActive
import kotlinx.coroutines.withContext
import ru.crown.finance.R
import ru.crown.finance.ledger.*
import java.time.LocalDate

private data class CalendarEdit(val expected: PlanningEvent?, val plan: CalendarPlan?, val name: String)
private data class CalendarResult(val expected: List<ExpectedPayment>, val account: AccountForecast?, val reserve: ReserveResult?, val links: ResolvedCalendarMatches)
@Composable
internal fun CalendarsPage(snapshot: LedgerSnapshot, commands: BookCommands, back: () -> Unit, showOperation: (Operation) -> Unit) {
    var selectedId by remember { mutableStateOf<String?>(null) }
    var version by remember { mutableStateOf<Long?>(null) }
    var edit by remember { mutableStateOf<CalendarEdit?>(null) }
    var history by remember { mutableStateOf(false) }
    val records = currentPlans(snapshot.planningEvents).filter { it.kind == PlanningKind.Calendar.wire }
    val current = records.firstOrNull { it.id == selectedId }
    val viewed = snapshot.planningEvents.firstOrNull { it.id == selectedId && it.sequence == version } ?: current
    var start by remember(selectedId) { mutableStateOf(snapshot.month.atDay(1).format(datePattern)) }
    var end by remember(selectedId) { mutableStateOf(snapshot.month.atEndOfMonth().format(datePattern)) }
    var accountId by remember(selectedId) { mutableStateOf(snapshot.balances.firstOrNull()?.account?.id) }
    var reserve by remember(selectedId) { mutableStateOf("0,00") }
    var result by remember(viewed?.sequence, start, end, accountId, reserve, snapshot.calendarMatchEvents, snapshot.history, snapshot.operations) { mutableStateOf<CalendarResult?>(null) }
    var error by remember { mutableStateOf<String?>(null) }
    var matching by remember(viewed?.sequence, start, end, accountId, snapshot.history, snapshot.operations) { mutableStateOf<Int?>(null) }
    val goBack = { if (selectedId == null) back() else { selectedId = null; version = null; history = false; matching = null } }
    BackHandler(selectedId != null && edit == null) { goBack() }
    LaunchedEffect(viewed?.sequence, start, end, accountId, reserve, snapshot.calendarMatchEvents, snapshot.history, snapshot.operations) {
        result = null; error = null
        if (viewed != null) try {
            val first = requireNotNull(dateValue(start)) { "Введите начало прогноза" }
            val last = requireNotNull(dateValue(end)) { "Введите окончание прогноза" }
            val threshold = requireNotNull(parseMinor(reserve)) { "Введите неотрицательный резерв" }
            val selectedAccount = accountId
            result = withContext(Dispatchers.Default) {
                val context = coroutineContext
                val plan = viewed.conditions() as CalendarPlan
                val expected = forecastPayments(plan, first, last) { context.ensureActive() }
                val operations = snapshot.currentOperations()
                val links = selectedAccount?.let { resolveCalendarMatches(viewed, it, expected, operations, snapshot.calendarMatchEvents) { context.ensureActive() } } ?: ResolvedCalendarMatches()
                val forecast = selectedAccount?.let { forecastAccount(plan, setOf(it), operations, first, last, links.matches) { context.ensureActive() } }
                CalendarResult(expected, forecast, forecast?.let { checkReserve(it, threshold) }, links)
            }
        } catch (cancel: CancellationException) { throw cancel }
        catch (failure: Exception) { error = failure.message ?: "Не удалось построить прогноз" }
    }
    Column(Modifier.fillMaxSize()) {
        LazyColumn(Modifier.weight(1f).testTag("calendars-list").semantics {
            stateDescription = if (viewed == null) "Список календарей" else if (error != null) "Ошибка расчёта" else if (result == null) "Идёт расчёт" else "Расчёт готов"
        }, contentPadding = PaddingValues(20.dp), verticalArrangement = Arrangement.spacedBy(12.dp)) {
            item {
                TextButton(goBack) { Icon(painterResource(R.drawable.ic_back), null); Spacer(Modifier.width(8.dp)); Text(if (viewed == null) "Отчёты" else "Календари") }
                WorkspaceTitle(viewed?.name ?: "Регулярные платежи")
            }
            if (viewed == null) {
                if (records.isEmpty()) item { Text("Добавьте расписания поступлений и списаний, чтобы построить прогноз.") }
                items(records, key = PlanningEvent::id) { row ->
                    OutlinedCard(onClick = { selectedId = row.id; version = null }, modifier = Modifier.fillMaxWidth()) {
                        Column(Modifier.padding(16.dp)) { Text(row.name, style = MaterialTheme.typography.titleMedium); if (row.archived) Text("В архиве") }
                    }
                }
            } else {
                item {
                    val versions = snapshot.planningEvents.filter { it.id == viewed.id }
                    if (viewed != current) Text("Прежние условия · версия " + (versions.indexOf(viewed) + 1))
                    if (viewed.archived) Text("Календарь в архиве")
                    if (viewed == current) TextButton({ edit = CalendarEdit(viewed, viewed.conditions() as CalendarPlan, viewed.name); commands.clearError() }) { Text("Изменить расписания") }
                    TextButton({ edit = CalendarEdit(null, viewed.conditions() as CalendarPlan, viewed.name.take(71) + " · копия"); commands.clearError() }) { Text("Создать копию") }
                    DateEntry(start, { start = it }, "Начало прогноза", commands.busy)
                    DateEntry(end, { end = it }, "Конец прогноза", commands.busy)
                    if (snapshot.balances.isNotEmpty()) Choice("Счёт прогноза", accountId, snapshot.balances.map { it.account.id }, { id -> snapshot.balances.first { it.account.id == id }.account.name }, !commands.busy) { accountId = it }
                    else Text("Для прогноза остатка добавьте счёт. Ожидаемые платежи доступны ниже.")
                    Entry(reserve, { reserve = it.take(24) }, "Резерв, ₽", commands.busy, KeyboardType.Decimal)
                    Text("План не меняет журнал. Сопоставьте проведённые платежи, чтобы исключить повторный учёт.")
                    error?.let { FormError(it) }
                    if (result == null && error == null) LinearProgressIndicator(Modifier.fillMaxWidth())
                }
                result?.let { calculated ->
                    calculated.account?.let { forecast ->
                        item {
                            Card(Modifier.fillMaxWidth()) { Column(Modifier.padding(16.dp), verticalArrangement = Arrangement.spacedBy(8.dp)) {
                                Text("Прогноз остатка", style = MaterialTheme.typography.labelLarge)
                                Text(money(forecast.closing), style = MaterialTheme.typography.headlineMedium)
                                Text("На начало: " + money(forecast.opening))
                                calculated.reserve?.let { check ->
                                    Text("Минимум: " + money(check.minimum) + " · " + check.on.format(datePattern))
                                    Text(check.firstBelow?.let { "Ниже резерва с " + it.format(datePattern) + " · Не хватает: " + money(check.shortfall) } ?: "Резерв соблюдён")
                                }
                            } }
                        }
                        item { Text("Дни изменения остатка", style = MaterialTheme.typography.titleMedium) }
                        items(forecast.days, key = { "day-" + it.date }) { day ->
                            Text(day.date.format(datePattern) + " · Факт " + money(day.booked) + " · План " + money(day.planned) + " · Остаток " + money(day.closing))
                        }
                    }
                    if (calculated.links.stale.isNotEmpty()) item {
                        FormError("Есть устаревшие сопоставления: операция изменена или отменена. Они не исключают ожидания из прогноза.")
                    }
                    items(calculated.links.stale, key = { "stale-" + it.sequence }) { event ->
                        OutlinedCard(Modifier.fillMaxWidth()) {
                            Column(Modifier.padding(12.dp)) {
                                Text("Устаревшее сопоставление", style = MaterialTheme.typography.titleSmall)
                                Text(LocalDate.parse(event.key.date).format(datePattern))
                                TextButton({
                                    commands.saveCalendarMatch(viewed, event.key, event, null) { matching = null }
                                }, enabled = !commands.busy) { Text("Снять устаревшую связь") }
                                snapshot.history.firstOrNull { it.id == event.operationId }?.current?.value?.let { source ->
                                    TextButton({ showOperation(source) }) { Text("Посмотреть источник связи") }
                                }
                            }
                        }
                    }
                    item { Text("Ожидаемые платежи · " + calculated.expected.size, style = MaterialTheme.typography.titleMedium) }
                    items(calculated.expected.indices.toList(), key = { "expected-" + it }) { index ->
                        val payment = calculated.expected[index]
                        val match = calculated.links.matches.firstOrNull { it.expectedIndex == index }
                        val saved = calculated.links.eventFor(payment)
                        val editable = viewed == current && !viewed.archived
                        OutlinedCard(Modifier.fillMaxWidth()) {
                            Column(Modifier.padding(12.dp)) {
                                Text(payment.schedule.name, style = MaterialTheme.typography.titleMedium)
                                Text(payment.date.format(datePattern) + " · " + money(payment.signed))
                                if (match != null) {
                                    Text("Сопоставлен с проведённой операцией")
                                    Text("Связь сохранена в книге", style = MaterialTheme.typography.bodySmall)
                                    TextButton({
                                        commands.saveCalendarMatch(viewed, requireNotNull(saved).key, saved, null) { matching = null }
                                    }, enabled = !commands.busy) { Text("Убрать сопоставление") }
                                } else if (calculated.account != null && editable) TextButton({
                                    matching = if (matching == index) null else index
                                }, enabled = !commands.busy) { Text("Сопоставить платёж " + (index + 1)) }
                                if (matching == index && match == null && editable) {
                                    val used = calculated.links.active.filter { it.key != saved?.key }.map { it.operationId }.toSet()
                                    val candidates = calculated.account?.booked.orEmpty().filter {
                                        it.id !in used && it.matchesPayment(requireNotNull(accountId), payment)
                                    }
                                    if (candidates.isEmpty()) Text("Нет свободных проведённых операций с той же датой и суммой")
                                    candidates.forEach { operation ->
                                        TextButton({
                                            commands.saveCalendarMatch(viewed, viewed.paymentKey(requireNotNull(accountId), payment), saved, operation) { matching = null }
                                        }, enabled = !commands.busy) { Text("Связать: " + operation.description) }
                                        TextButton({ showOperation(operation) }) { Text("Посмотреть операцию") }
                                    }
                                }
                            }
                        }
                    }
                }
                item { TextButton({ history = !history }) { Text(if (history) "Скрыть историю календаря" else "История календаря") } }
                if (history) {
                    val versions = snapshot.planningEvents.filter { it.id == selectedId }
                    items(versions.reversed(), key = { "version-" + it.sequence }) { row ->
                        TextButton({ version = row.sequence }) { Text("Версия " + (versions.indexOf(row) + 1) + " · " + row.name + if (row.archived) " · архив" else "") }
                    }
                }
            }
        }
        commands.error?.let { FormError(it) }
        Button({ edit = CalendarEdit(null, null, ""); commands.clearError() }, Modifier.fillMaxWidth().padding(horizontal = 20.dp, vertical = 8.dp), enabled = !commands.busy) { Text("Новый календарь") }
    }
    edit?.let { value -> CalendarEditor(value.expected, value.plan, value.name, commands, { edit = null }) { id -> edit = null; selectedId = id; version = null; history = false } }
}
