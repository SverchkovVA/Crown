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
import androidx.compose.ui.unit.dp
import kotlinx.coroutines.CancellationException
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.ensureActive
import kotlinx.coroutines.withContext
import ru.crown.finance.R
import ru.crown.finance.ledger.*
import java.math.BigDecimal

private data class PlanEdit(val expected: PlanningEvent?, val value: PlanningConditions?, val name: String = "")
private data class PlanResult(val budget: BudgetExecution? = null, val goal: GoalProjection? = null, val account: GoalAccountProgress? = null)

@Composable
internal fun PlanningPage(snapshot: LedgerSnapshot, commands: BookCommands, back: () -> Unit, showOperation: (Operation) -> Unit) {
    var kind by remember { mutableStateOf(PlanningKind.Budget) }
    var selectedId by remember { mutableStateOf<String?>(null) }
    var version by remember { mutableStateOf<Long?>(null) }
    var edit by remember { mutableStateOf<PlanEdit?>(null) }
    var history by remember { mutableStateOf(false) }
    var accountId by remember(selectedId) { mutableStateOf<String?>(null) }
    var result by remember { mutableStateOf<PlanResult?>(null) }
    var error by remember { mutableStateOf<String?>(null) }
    val current = currentPlans(snapshot.planningEvents).firstOrNull { it.id == selectedId }
    val viewed = snapshot.planningEvents.firstOrNull { it.id == selectedId && it.sequence == version } ?: current
    val goBack = { if (selectedId != null) { selectedId = null; version = null; history = false } else back() }
    BackHandler(selectedId != null && edit == null) { goBack() }
    LaunchedEffect(viewed?.payload, snapshot.history, snapshot.operations, accountId) {
        result = null; error = null
        if (viewed != null) try {
            result = withContext(Dispatchers.Default) {
                val context = coroutineContext
                val operations = snapshot.currentOperations()
                when (val value = viewed.conditions()) {
                    is BudgetPlan -> PlanResult(budget = executeBudget(value, operations) { context.ensureActive() })
                    is GoalPlan -> if (accountId == null) PlanResult(goal = projectGoal(value)) else {
                        val account = snapshot.balances.first { it.account.id == accountId }.account
                        val progress = goalAccountProgress(value, account, operations)
                        PlanResult(goal = progress.projection, account = progress)
                    }
                }
            }
        } catch (cancel: CancellationException) { throw cancel }
        catch (failure: Exception) { error = failure.message ?: "Не удалось построить расчёт" }
    }
    Column(Modifier.fillMaxSize()) {
        LazyColumn(Modifier.weight(1f).testTag("planning-list").semantics {
            stateDescription = if (viewed == null) "Список планов" else if (error != null) "Ошибка расчёта" else if (result == null) "Идёт расчёт" else "Расчёт готов"
        }, contentPadding = PaddingValues(20.dp), verticalArrangement = Arrangement.spacedBy(12.dp)) {
            item {
                TextButton(goBack) { Icon(painterResource(R.drawable.ic_back), null); Spacer(Modifier.width(8.dp)); Text(if (viewed == null) "Отчёты" else kind.label) }
                WorkspaceTitle(viewed?.name ?: "Бюджеты и цели")
                if (viewed == null) Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                    PlanningKind.entries.forEach { value -> FilterChip(selected = kind == value, onClick = { kind = value }, label = { Text(value.label) }) }
                }
            }
            if (viewed == null) {
                val rows = currentPlans(snapshot.planningEvents).filter { it.kind == kind.wire }
                if (rows.isEmpty()) item { Text(if (kind == PlanningKind.Budget) "Задайте лимит расходов и выберите период." else "Укажите сумму цели и план ежемесячных пополнений.") }
                items(rows, key = PlanningEvent::id) { event ->
                    OutlinedCard(onClick = { selectedId = event.id; version = null }, modifier = Modifier.fillMaxWidth()) {
                        Column(Modifier.padding(16.dp)) {
                            Text(event.name, style = MaterialTheme.typography.titleMedium)
                            if (event.archived) Text("В архиве")
                        }
                    }
                }
            } else {
                item {
                    val number = snapshot.planningEvents.filter { it.id == viewed.id }.indexOf(viewed) + 1
                    if (viewed.sequence != current?.sequence) Text("Прежние условия · версия " + number)
                    if (viewed.archived) Text("План в архиве")
                    Row {
                        if (viewed.sequence == current?.sequence) TextButton({ edit = PlanEdit(viewed, viewed.conditions(), viewed.name); commands.clearError() }) { Text("Изменить условия") }
                        TextButton({ edit = PlanEdit(null, viewed.conditions(), viewed.name.take(71) + " · копия"); commands.clearError() }) { Text("Создать копию") }
                    }
                    if (viewed.kind == PlanningKind.Goal.wire && snapshot.balances.isNotEmpty()) {
                        Choice("Накопления", accountId != null, listOf(false, true), { if (it) "По остатку счёта" else "Из сохранённых условий" }, !commands.busy) {
                            accountId = if (it) snapshot.balances.first().account.id else null
                        }
                        if (accountId != null) Choice("Счёт цели", accountId, snapshot.balances.map { it.account.id },
                            { id -> snapshot.balances.first { it.account.id == id }.account.name }, !commands.busy) { accountId = it }
                    }
                    error?.let { FormError(it) }
                    if (result == null && error == null) LinearProgressIndicator(Modifier.fillMaxWidth())
                }
                result?.budget?.let { budget ->
                    item {
                        Card(Modifier.fillMaxWidth()) {
                            Column(Modifier.padding(16.dp), verticalArrangement = Arrangement.spacedBy(8.dp)) {
                                Text("Расходы за период", style = MaterialTheme.typography.labelLarge)
                                Text(money(budget.spent), style = MaterialTheme.typography.headlineMedium)
                                Text("Лимит: " + money(budget.plan.limit))
                                Text(if (budget.remaining.signum() < 0) "Превышение: " + money(-budget.remaining) else "Осталось: " + money(budget.remaining))
                            }
                        }
                        Text(budget.plan.start.format(datePattern) + " — " + budget.plan.end.format(datePattern))
                        Text("Возвраты уменьшают расходы, комиссии входят в расчёт.")
                    }
                    item { Text("Операции в расчёте · " + budget.sources.size, style = MaterialTheme.typography.titleMedium) }
                    items(budget.sources, key = { it.operation.id }) { source ->
                        OutlinedCard(onClick = { showOperation(source.operation) }, modifier = Modifier.fillMaxWidth()) {
                            Column(Modifier.padding(12.dp)) {
                                Text(source.operation.description)
                                Text(source.operation.date.format(datePattern) + " · Учтено: " + money(source.spent))
                            }
                        }
                    }
                }
                result?.goal?.let { goal ->
                    item {
                        Card(Modifier.fillMaxWidth()) {
                            Column(Modifier.padding(16.dp), verticalArrangement = Arrangement.spacedBy(8.dp)) {
                                Text("Накоплено", style = MaterialTheme.typography.labelLarge)
                                Text(money(goal.plan.current), style = MaterialTheme.typography.headlineMedium)
                                Text("Цель: " + money(goal.plan.target))
                                Text("Прогресс: " + BigDecimal(goal.basisPoints).movePointLeft(2).toPlainString().replace('.', ',') + "%")
                                Text("Осталось: " + money(goal.remaining))
                            }
                        }
                    }
                    item {
                        Text("Ежемесячное пополнение: " + money(goal.plan.monthly))
                        when {
                            goal.remaining.signum() == 0 -> Text("Цель уже достигнута")
                            goal.contributions == null -> Text("При текущем пополнении дата достижения не определена")
                            goal.reachedOn == null -> Text("Дата достижения выходит за пределы календаря")
                            else -> Text("Достижение: " + goal.reachedOn.format(datePattern) + " · Взносов: " + goal.contributions)
                        }
                        goal.plan.deadline?.let { deadline ->
                            Text("Срок: " + deadline.format(datePattern))
                            Text("Взносов до срока: " + goal.byDeadline)
                            Text(goal.requiredMonthly?.let { "Необходимо в месяц: " + money(it) } ?: "До срока нет ежемесячных пополнений")
                            Text("К сроку: " + money(requireNotNull(goal.atDeadline)))
                        }
                    }
                    result?.account?.let { progress ->
                        item {
                            Text("Остаток «" + progress.account.name + "» на " + goal.plan.asOf.format(datePattern))
                            Text("Выбор счёта действует в открытом отчёте. Сохранённые условия цели не меняются.")
                            Text("Источники остатка · " + progress.sources.size, style = MaterialTheme.typography.titleMedium)
                        }
                        items(progress.sources, key = Operation::id) { operation ->
                            TextButton({ showOperation(operation) }) { Text(operation.date.format(datePattern) + " · " + operation.description) }
                        }
                    }
                }
                item { TextButton({ history = !history }) { Text(if (history) "Скрыть историю плана" else "История плана") } }
                if (history) {
                    val versions = snapshot.planningEvents.filter { it.id == selectedId }
                    items(versions.reversed(), key = { "version-" + it.sequence }) { event ->
                        TextButton({ version = event.sequence }) { Text("Версия " + (versions.indexOf(event) + 1) + " · " + event.name + if (event.archived) " · архив" else "") }
                    }
                }
            }
        }
        commands.error?.let { FormError(it) }
        Button({ edit = PlanEdit(null, null); commands.clearError() }, Modifier.fillMaxWidth().padding(horizontal = 20.dp, vertical = 8.dp), enabled = !commands.busy) {
            Text(if (kind == PlanningKind.Budget) "Новый бюджет" else "Новая цель")
        }
    }
    edit?.let { value ->
        val close = { edit = null }
        val saved: (String) -> Unit = { id -> edit = null; selectedId = id; version = null; history = false }
        if (kind == PlanningKind.Budget) BudgetEditor(value.expected, value.value as? BudgetPlan, value.name, snapshot, commands, close, saved)
        else GoalEditor(value.expected, value.value as? GoalPlan, value.name, commands, close, saved)
    }
}
