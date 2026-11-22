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

private data class CalendarEdit(val expected: PlanningEvent?, val plan: CalendarPlan?, val name: String)
private data class CalendarResult(val expected: List<ExpectedPayment>, val account: AccountForecast?, val reserve: ReserveResult?)
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
    var matches by remember(viewed?.payload, start, end, accountId, snapshot.history, snapshot.operations) { mutableStateOf<List<PaymentMatch>>(emptyList()) }
    var result by remember(viewed?.payload, start, end, accountId, reserve, matches, snapshot.history, snapshot.operations) { mutableStateOf<CalendarResult?>(null) }
    var error by remember { mutableStateOf<String?>(null) }
    var matching by remember(viewed?.payload, start, end, accountId, snapshot.history, snapshot.operations) { mutableStateOf<Int?>(null) }
    val goBack = { if (selectedId == null) back() else { selectedId = null; version = null; history = false; matching = null } }
    BackHandler(selectedId != null && edit == null) { goBack() }
    LaunchedEffect(viewed?.payload, start, end, accountId, reserve, matches, snapshot.history, snapshot.operations) {
        result = null; error = null
        if (viewed != null) try {
            val first = requireNotNull(dateValue(start)) { "Введите начало прогноза" }
            val last = requireNotNull(dateValue(end)) { "Введите окончание прогноза" }
            val threshold = requireNotNull(parseMinor(reserve)) { "Введите неотрицательный резерв" }
            val selectedAccount = accountId
            result = withContext(Dispatchers.Default) {
                val context = coroutineContext
                val plan = viewed.conditions() as CalendarPlan
                val forecast = selectedAccount?.let { forecastAccount(plan, setOf(it), snapshot.currentOperations(), first, last, matches) { context.ensureActive() } }
                val expected = forecast?.expected ?: forecastPayments(plan, first, last) { context.ensureActive() }
                CalendarResult(expected, forecast, forecast?.let { checkReserve(it, threshold) })
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
                    item { Text("Ожидаемые платежи · " + calculated.expected.size, style = MaterialTheme.typography.titleMedium) }
                    items(calculated.expected.indices.toList(), key = { "expected-" + it }) { index ->
                        val payment = calculated.expected[index]
                        val match = matches.firstOrNull { it.expectedIndex == index }
                        OutlinedCard(Modifier.fillMaxWidth()) {
                            Column(Modifier.padding(12.dp)) {
                                Text(payment.schedule.name, style = MaterialTheme.typography.titleMedium)
                                Text(payment.date.format(datePattern) + " · " + money(payment.signed))
                                if (match != null) {
                                    Text("Сопоставлен с проведённой операцией")
                                    TextButton({ matches = matches.filterNot { it.expectedIndex == index } }) { Text("Убрать сопоставление") }
                                } else if (calculated.account != null) TextButton({ matching = if (matching == index) null else index }) { Text("Сопоставить платёж " + (index + 1)) }
                                if (matching == index && match == null) {
                                    val candidates = calculated.account?.booked.orEmpty().filter {
                                        it.id !in matches.map(PaymentMatch::operationId) && it.date == payment.date && (it.accountId == accountId || it.destinationId == accountId) &&
                                            it.movementFor(requireNotNull(accountId)).toBigInteger() == payment.signed
                                    }
                                    if (candidates.isEmpty()) Text("Нет проведённых операций с той же датой и суммой")
                                    candidates.forEach { operation ->
                                        TextButton({ matches = matches + PaymentMatch(index, operation.id, operation.fingerprint()); matching = null }) { Text("Связать: " + operation.description) }
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
