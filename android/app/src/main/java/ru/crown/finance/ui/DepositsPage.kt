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
import androidx.compose.ui.unit.dp
import androidx.compose.ui.semantics.contentDescription
import androidx.compose.ui.semantics.semantics
import kotlinx.coroutines.CancellationException
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.ensureActive
import kotlinx.coroutines.withContext
import ru.crown.finance.R
import ru.crown.finance.ledger.*
import java.time.LocalDate
import java.util.UUID

private data class DepositEdit(val expected: DepositEvent?, val scenario: DepositScenario?, val name: String = "")

@Composable
internal fun DepositsPage(snapshot: LedgerSnapshot, commands: BookCommands, back: () -> Unit) {
    var selectedId by remember { mutableStateOf<String?>(null) }
    var version by remember { mutableStateOf<Long?>(null) }
    var editing by remember { mutableStateOf<DepositEdit?>(null) }
    var daily by remember { mutableStateOf(false) }
    var history by remember { mutableStateOf(false) }
    var forecast by remember { mutableStateOf<DepositForecast?>(null) }
    var failure by remember { mutableStateOf<String?>(null) }
    val records = currentDeposits(snapshot.depositEvents)
    val current = records.firstOrNull { it.id == selectedId }
    val viewed = snapshot.depositEvents.firstOrNull { it.id == selectedId && it.sequence == version } ?: current
    val goBack = { if (selectedId != null) { selectedId = null; version = null; history = false; daily = false } else back() }
    BackHandler(selectedId != null && editing == null) { goBack() }
    LaunchedEffect(viewed?.payload) {
        forecast = null; failure = null; daily = false
        if (viewed != null) try {
            forecast = withContext(Dispatchers.Default) {
                val context = coroutineContext
                forecastDeposit(viewed.scenario()) { context.ensureActive() }
            }
        } catch (error: CancellationException) { throw error }
        catch (error: Exception) { failure = error.message ?: "Не удалось рассчитать сценарий" }
    }
    val payments = remember(forecast) {
        forecast?.let { result -> val dates = result.scenario.payouts.toSet(); result.days.filter { it.date in dates } }.orEmpty()
    }
    Column(Modifier.fillMaxSize()) {
        LazyColumn(Modifier.weight(1f).testTag("deposits-list"), contentPadding = PaddingValues(20.dp), verticalArrangement = Arrangement.spacedBy(12.dp)) {
            item {
                TextButton(goBack) { Icon(painterResource(R.drawable.ic_back), null); Spacer(Modifier.width(8.dp)); Text(if (selectedId == null) "Отчёты" else "Сценарии") }
                WorkspaceTitle(viewed?.name ?: "Вклады")
                Text("Прогноз процентов по вашим условиям. Фактические остатки книги не меняются.")
            }
            if (viewed == null) {
                if (records.isEmpty()) item { Text("Создайте сценарий: сумма, срок и ставка. Условия можно уточнять и сохранять.") }
                items(records, key = DepositEvent::id) { event ->
                    OutlinedCard(onClick = { selectedId = event.id; version = null }, modifier = Modifier.fillMaxWidth()) {
                        Column(Modifier.padding(16.dp)) {
                            Text(event.name, style = MaterialTheme.typography.titleMedium)
                            if (event.archived) Text("В архиве")
                            val value = remember(event.payload) { event.scenario() }
                            Text(value.opened.format(datePattern) + " — " + value.matures.format(datePattern))
                            Text("Начальная сумма: " + money(value.initial))
                        }
                    }
                }
            } else {
                item {
                    if (viewed.sequence != current?.sequence) Text("Просмотр прежних условий · версия " + viewed.sequence)
                    if (viewed.archived) Text("Сценарий в архиве")
                    Row {
                        if (viewed.sequence == current?.sequence) TextButton({ editing = DepositEdit(viewed, viewed.scenario(), viewed.name); commands.clearError() }) { Text("Изменить условия") }
                        TextButton({ editing = DepositEdit(null, viewed.scenario(), viewed.name.take(71) + " · копия"); commands.clearError() }) { Text("Создать копию") }
                    }
                    failure?.let { FormError(it) }
                    if (forecast == null && failure == null) LinearProgressIndicator(Modifier.fillMaxWidth())
                }
                forecast?.let { result ->
                    item {
                        Card(Modifier.fillMaxWidth()) {
                            Column(Modifier.padding(16.dp), verticalArrangement = Arrangement.spacedBy(8.dp)) {
                                Text("Прогноз процентов", style = MaterialTheme.typography.labelLarge)
                                Text(money(result.interest), style = MaterialTheme.typography.headlineMedium)
                                Text("Сумма вклада в конце: " + money(result.closing))
                                Text(if (result.scenario.capitalize) "Проценты прибавляются к вкладу" else "Выплаты учитываются отдельно от суммы вклада")
                            }
                        }
                    }
                    item { Text("Предполагаемые выплаты", style = MaterialTheme.typography.titleMedium) }
                    items(payments, key = { it.date.toString() }) { day ->
                        Row(Modifier.fillMaxWidth(), horizontalArrangement = Arrangement.SpaceBetween) { Text(day.date.format(datePattern)); Text(money(day.payout)) }
                    }
                    item { TextButton({ daily = !daily }) { Text(if (daily) "Скрыть расчёт по дням" else "Расчёт по дням") } }
                    if (daily) items(result.days, key = { "day-" + it.date }) { day ->
                        Column {
                            Text(day.date.format(datePattern), style = MaterialTheme.typography.titleSmall)
                            Text("База: " + money(day.base) + " · Движение: " + money(day.movement))
                            Text("Выплата: " + money(day.payout) + " · Остаток: " + money(day.closing))
                        }
                    }
                }
                item { TextButton({ history = !history }) { Text(if (history) "Скрыть историю условий" else "История условий") } }
                if (history) items(snapshot.depositEvents.filter { it.id == selectedId }.reversed(), key = { "version-" + it.sequence }) { event ->
                    TextButton({ version = event.sequence }) { Text("Версия " + event.sequence + " · " + event.name + if (event.archived) " · архив" else "") }
                }
            }
        }
        commands.error?.let { FormError(it) }
        Button({ editing = DepositEdit(null, null); commands.clearError() }, Modifier.fillMaxWidth().padding(horizontal = 20.dp, vertical = 8.dp), enabled = !commands.busy) { Text("Новый сценарий вклада") }
    }
    editing?.let { edit ->
        DepositEditor(edit.expected, edit.scenario, edit.name, commands, close = { editing = null }) { id ->
            editing = null; selectedId = id; version = null; history = false
        }
    }
}

private data class RateDraft(val id: String = UUID.randomUUID().toString(), val date: String, val percent: String)
private data class MoveDraft(val id: String = UUID.randomUUID().toString(), val date: String, val amount: String, val withdrawal: Boolean)

@Composable
internal fun DepositEditor(
    expected: DepositEvent?, initial: DepositScenario?, initialName: String,
    commands: BookCommands, close: () -> Unit, saved: (String) -> Unit,
) {
    val id = remember { expected?.id ?: UUID.randomUUID().toString() }
    val start = initial?.opened ?: LocalDate.now()
    var name by remember { mutableStateOf(initialName) }
    var opened by remember { mutableStateOf(start.format(datePattern)) }
    var matures by remember { mutableStateOf((initial?.matures ?: start.plusYears(1)).format(datePattern)) }
    var amount by remember { mutableStateOf(initial?.let { amountText(it.initial) }.orEmpty()) }
    var minimum by remember { mutableStateOf(amountText(initial?.minimum ?: 0)) }
    var rates by remember { mutableStateOf(initial?.rates?.sortedBy { it.date }?.map { RateDraft(it.id, it.date.format(datePattern), it.percent.toPlainString()) } ?: listOf(RateDraft(date = opened, percent = ""))) }
    var moves by remember { mutableStateOf<List<MoveDraft>>(initial?.movements?.map { MoveDraft(it.id, it.date.format(datePattern), amountText(kotlin.math.abs(it.minor)), it.minor < 0) } ?: emptyList()) }
    val initialMonthly = initial?.let { monthlyDepositPayouts(it.opened, it.matures) }
    var payoutMode by remember { mutableStateOf(if (initial == null || initial.payouts.size == 1) "В конце срока" else if (initial.payouts.sorted() == initialMonthly) "Ежемесячно от открытия" else "Даты вручную") }
    var dates by remember { mutableStateOf<List<String>>(initial?.payouts?.filter { it != initial.matures }?.map { it.format(datePattern) } ?: emptyList()) }
    var boundary by remember { mutableStateOf(initial?.boundary ?: AccrualBoundary.MaturityIncluded) }
    var moment by remember { mutableStateOf(initial?.moment ?: BalanceMoment.After) }
    var count by remember { mutableStateOf(initial?.dayCount ?: DepositDayCount.CalendarYear) }
    var rounding by remember { mutableStateOf(initial?.rounding ?: DepositRounding.HalfUp) }
    var stage by remember { mutableStateOf(initial?.roundingStage ?: RoundingStage.Payout) }
    var capitalize by remember { mutableStateOf(initial?.capitalize ?: false) }
    var topups by remember { mutableStateOf(initial?.allowTopups ?: false) }
    var withdrawals by remember { mutableStateOf(initial?.allowWithdrawals ?: false) }
    var archived by remember { mutableStateOf(expected?.archived ?: false) }
    var advanced by remember { mutableStateOf(false) }
    var showRates by remember { mutableStateOf(false) }
    var showMoves by remember { mutableStateOf(false) }
    var error by remember { mutableStateOf<String?>(null) }
    LaunchedEffect(name, opened, matures, amount, minimum, rates, moves, payoutMode, dates, boundary, moment, count, rounding, stage, capitalize, topups, withdrawals, archived) { error = null; commands.clearError() }
    fun scenario(): DepositScenario {
        val first = requireNotNull(dateValue(opened)) { "Проверьте дату открытия" }
        val last = requireNotNull(dateValue(matures)) { "Проверьте дату окончания" }
        val cash = requireNotNull(parseMinor(amount)) { "Проверьте начальную сумму" }
        val floor = requireNotNull(parseMinor(minimum)) { "Проверьте неснижаемый остаток" }
        val versions = rates.mapIndexed { index, draft ->
            DepositRate(draft.id, if (index == 0) first else requireNotNull(dateValue(draft.date)) { "Проверьте дату ставки " + (index + 1) },
                requireNotNull(parseDepositRate(draft.percent)) { "Проверьте ставку " + (index + 1) + ": от 0 до 1000%, до 8 дробных знаков" })
        }
        val movements = moves.mapIndexed { index, draft ->
            val value = requireNotNull(parseMinor(draft.amount)) { "Проверьте сумму движения " + (index + 1) }
            DepositMovement(draft.id, requireNotNull(dateValue(draft.date)) { "Проверьте дату движения " + (index + 1) }, if (draft.withdrawal) -value else value)
        }
        val payouts = when (payoutMode) {
            "В конце срока" -> listOf(last)
            "Ежемесячно от открытия" -> monthlyDepositPayouts(first, last)
            else -> dates.map { text -> requireNotNull(dateValue(text)) { "Проверьте дату выплаты" }.also { require(it < last) { "Промежуточная выплата должна быть раньше окончания" } } } + last
        }
        return DepositScenario(first, last, cash, versions, movements, payouts, boundary, moment, count, rounding, stage, capitalize, topups, withdrawals, floor).also { it.validate() }
    }
    Editor(if (expected == null) "Новый сценарий вклада" else "Условия вклада", commands, error, close, "Сохранить сценарий", {
        try {
            require(name.isNotBlank()) { "Введите название сценария" }
            commands.saveDeposit(id, name.trim(), scenario(), expected, archived) { saved(id) }
        } catch (problem: IllegalArgumentException) { error = problem.message ?: "Проверьте условия" }
    }) {
        Entry(name, { name = it.take(80) }, "Название сценария", commands.busy)
        Entry(amount, { amount = it.take(24) }, "Сумма вклада, ₽", commands.busy, androidx.compose.ui.text.input.KeyboardType.Decimal)
        DateEntry(opened, { opened = it }, "Открытие", commands.busy)
        DateEntry(matures, { matures = it }, "Окончание", commands.busy)
        Entry(rates.first().percent, { value -> rates = rates.toMutableList().also { it[0] = it[0].copy(percent = value.take(40)) } }, "Начальная ставка, %", commands.busy, androidx.compose.ui.text.input.KeyboardType.Decimal)
        Choice("Выплаты", payoutMode, listOf("В конце срока", "Ежемесячно от открытия", "Даты вручную"), { it }, !commands.busy) { payoutMode = it }
        if (payoutMode == "Даты вручную") {
            Text("Последняя выплата — в дату окончания. Здесь добавьте только промежуточные.")
            dates.forEachIndexed { index, text ->
                DateEntry(text, { value -> dates = dates.toMutableList().also { it[index] = value } }, "Выплата " + (index + 1), commands.busy)
                TextButton({ dates = dates.filterIndexed { i, _ -> i != index } }, enabled = !commands.busy) { Text("Убрать выплату " + (index + 1)) }
            }
            TextButton({ dates = dates + opened }, enabled = !commands.busy && dates.size < 9999) { Text("Добавить дату выплаты") }
        }
        DepositSwitch("Капитализация процентов", capitalize, commands.busy) { capitalize = it }
        TextButton({ showRates = !showRates }) { Text("Изменения ставки · " + (rates.size - 1)) }
        if (showRates) {
            rates.drop(1).forEachIndexed { offset, rate ->
                val index = offset + 1
                DateEntry(rate.date, { value -> rates = rates.toMutableList().also { it[index] = rate.copy(date = value) } }, "Дата ставки " + (index + 1), commands.busy)
                Entry(rate.percent, { value -> rates = rates.toMutableList().also { it[index] = rate.copy(percent = value.take(40)) } }, "Ставка " + (index + 1) + ", %", commands.busy, androidx.compose.ui.text.input.KeyboardType.Decimal)
                TextButton({ rates = rates.filterIndexed { i, _ -> i != index } }, enabled = !commands.busy) { Text("Убрать ставку " + (index + 1)) }
            }
            TextButton({ rates = rates + RateDraft(date = matures, percent = "") }, enabled = !commands.busy && rates.size < 10000) { Text("Добавить ставку") }
        }
        TextButton({ showMoves = !showMoves }) { Text("Пополнения и снятия · " + moves.size) }
        if (showMoves) {
            DepositSwitch("Пополнение разрешено", topups, commands.busy) { topups = it }
            DepositSwitch("Снятие разрешено", withdrawals, commands.busy) { withdrawals = it }
            moves.forEachIndexed { index, move ->
                OutlinedCard {
                    Column(Modifier.padding(12.dp), verticalArrangement = Arrangement.spacedBy(8.dp)) {
                        Text("Движение " + (index + 1))
                        Choice("Вид движения " + (index + 1), move.withdrawal, listOf(false, true), { if (it) "Снятие" else "Пополнение" }, !commands.busy) { value -> moves = moves.toMutableList().also { it[index] = move.copy(withdrawal = value) } }
                        DateEntry(move.date, { value -> moves = moves.toMutableList().also { it[index] = move.copy(date = value) } }, "Дата движения " + (index + 1), commands.busy)
                        Entry(move.amount, { value -> moves = moves.toMutableList().also { it[index] = move.copy(amount = value.take(24)) } }, "Движение " + (index + 1) + ", ₽", commands.busy, androidx.compose.ui.text.input.KeyboardType.Decimal)
                        TextButton({ moves = moves.filterIndexed { i, _ -> i != index } }, enabled = !commands.busy) { Text("Убрать движение " + (index + 1)) }
                    }
                }
            }
            TextButton({ moves = moves + MoveDraft(date = opened, amount = "", withdrawal = false) }, enabled = !commands.busy && moves.size < 10000) { Text("Добавить движение") }
        }
        TextButton({ advanced = !advanced }) { Text(if (advanced) "Скрыть правила расчёта" else "Правила расчёта") }
        if (advanced) {
            Choice("Граница начисления", boundary, AccrualBoundary.entries, AccrualBoundary::label, !commands.busy) { boundary = it }
            Choice("Остаток для процентов", moment, BalanceMoment.entries, BalanceMoment::label, !commands.busy) { moment = it }
            Choice("Дни в году", count, DepositDayCount.entries, DepositDayCount::label, !commands.busy) { count = it }
            Choice("Округление", rounding, DepositRounding.entries, DepositRounding::label, !commands.busy) { rounding = it }
            Choice("Когда округлять", stage, RoundingStage.entries, RoundingStage::label, !commands.busy) { stage = it }
            Entry(minimum, { minimum = it.take(24) }, "Неснижаемый остаток, ₽", commands.busy, androidx.compose.ui.text.input.KeyboardType.Decimal)
        }
        if (expected != null) DepositSwitch("Сценарий в архиве", archived, commands.busy) { archived = it }
        Text("Это предположения. Реальные выплаты вводятся в журнал операций отдельно.")
    }
}

@Composable
private fun DepositSwitch(label: String, checked: Boolean, busy: Boolean, change: (Boolean) -> Unit) {
    Row(Modifier.fillMaxWidth(), horizontalArrangement = Arrangement.SpaceBetween) {
        Text(label, Modifier.weight(1f))
        Switch(checked, change, enabled = !busy, modifier = Modifier.testTag(label).semantics { contentDescription = label })
    }
}
