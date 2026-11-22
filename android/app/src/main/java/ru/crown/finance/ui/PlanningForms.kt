package ru.crown.finance.ui

import androidx.compose.foundation.layout.*
import androidx.compose.foundation.selection.toggleable
import androidx.compose.material3.*
import androidx.compose.runtime.*
import androidx.compose.ui.Modifier
import androidx.compose.ui.semantics.Role
import androidx.compose.ui.semantics.contentDescription
import androidx.compose.ui.semantics.semantics
import androidx.compose.ui.text.input.KeyboardType
import androidx.compose.ui.unit.dp
import ru.crown.finance.ledger.*
import java.time.LocalDate
import java.time.YearMonth
import java.util.UUID

@Composable
internal fun BudgetEditor(expected: PlanningEvent?, initial: BudgetPlan?, title: String, snapshot: LedgerSnapshot, commands: BookCommands, close: () -> Unit, saved: (String) -> Unit) {
    val id = remember { expected?.id ?: UUID.randomUUID().toString() }
    var name by remember { mutableStateOf(title) }
    var limit by remember { mutableStateOf(initial?.let { amountText(it.limit) }.orEmpty()) }
    var start by remember { mutableStateOf((initial?.start ?: snapshot.month.atDay(1)).format(datePattern)) }
    var end by remember { mutableStateOf((initial?.end ?: snapshot.month.atEndOfMonth()).format(datePattern)) }
    var monthly by remember { mutableStateOf(initial?.monthly ?: true) }
    var accounts by remember { mutableStateOf<Set<String>?>(initial?.accounts) }
    var categories by remember { mutableStateOf<Set<String?>?>(initial?.categories) }
    var archived by remember { mutableStateOf(expected?.archived ?: false) }
    var showScope by remember { mutableStateOf(false) }
    var error by remember { mutableStateOf<String?>(null) }
    LaunchedEffect(name, limit, start, end, monthly, accounts, categories, archived) { error = null; commands.clearError() }
    Editor(if (expected == null) "Новый бюджет" else "Условия бюджета", commands, error, close, "Сохранить бюджет", {
        try {
            require(name.isNotBlank()) { "Введите название бюджета" }
            val date = requireNotNull(dateValue(start)) { "Проверьте начало периода" }
            val period = YearMonth.from(date)
            val first = if (monthly) period.atDay(1) else date
            val last = if (monthly) period.atEndOfMonth() else requireNotNull(dateValue(end)) { "Проверьте окончание периода" }
            val money = requireNotNull(parseMinor(limit)) { "Введите неотрицательный лимит" }
            val plan = BudgetPlan(first, last, money, accounts, categories, monthly).also { it.validate() }
            commands.savePlan(id, name.trim(), plan, expected, archived) { saved(id) }
        } catch (failure: IllegalArgumentException) { error = failure.message ?: "Проверьте условия бюджета" }
    }) {
        Entry(name, { name = it.take(80) }, "Название бюджета", commands.busy)
        Entry(limit, { limit = it.take(24) }, "Лимит расходов, ₽", commands.busy, KeyboardType.Decimal)
        PlanSwitch("Календарный месяц", monthly, commands.busy) { monthly = it }
        DateEntry(start, { start = it }, if (monthly) "Любая дата месяца" else "Начало периода", commands.busy)
        if (!monthly) DateEntry(end, { end = it }, "Конец периода", commands.busy)
        else dateValue(start)?.let { Text("Период: " + YearMonth.from(it).atDay(1).format(datePattern) + " — " + YearMonth.from(it).atEndOfMonth().format(datePattern)) }
        TextButton({ showScope = !showScope }) { Text(if (showScope) "Скрыть отбор" else "Счета и категории") }
        if (showScope) {
            PlanSwitch("Все счета", accounts == null, commands.busy) { accounts = if (it) null else emptySet() }
            if (accounts != null) {
                snapshot.balances.forEach { row ->
                    PlanCheck("Счёт бюджета: " + row.account.name, row.account.name, row.account.id in accounts.orEmpty(), commands.busy) {
                        accounts = if (it) accounts.orEmpty() + row.account.id else accounts.orEmpty() - row.account.id
                    }
                }
                if (accounts.isNullOrEmpty()) Text("Счета не выбраны: результат будет нулевым")
            }
            PlanSwitch("Все категории", categories == null, commands.busy) { categories = if (it) null else emptySet() }
            if (categories != null) {
                Text("Подкатегории выбираются отдельно.")
                val values = listOf<String?>(null) + snapshot.categories.filter { it.kind == OperationKind.Expense }.map { it.id }
                values.forEach { identifier ->
                    val category = snapshot.categories.firstOrNull { it.id == identifier }
                    val label = category?.let { value ->
                        val parent = snapshot.categories.firstOrNull { it.id == value.parentId }
                        if (parent == null) value.name else parent.name + " / " + value.name
                    } ?: "Без категории"
                    PlanCheck("Категория бюджета: " + label, label, identifier in categories.orEmpty(), commands.busy) {
                        categories = if (it) categories.orEmpty() + identifier else categories.orEmpty() - identifier
                    }
                }
                if (categories.isNullOrEmpty()) Text("Категории не выбраны: результат будет нулевым")
            }
        }
        if (expected != null) PlanSwitch("План в архиве", archived, commands.busy) { archived = it }
        Text("Бюджет сравнивается с записанными расходами и возвратами. Новые операции не создаются.")
    }
}

@Composable
internal fun GoalEditor(expected: PlanningEvent?, initial: GoalPlan?, title: String, commands: BookCommands, close: () -> Unit, saved: (String) -> Unit) {
    val id = remember { expected?.id ?: UUID.randomUUID().toString() }
    var name by remember { mutableStateOf(title) }
    var date by remember { mutableStateOf((initial?.asOf ?: LocalDate.now()).format(datePattern)) }
    var current by remember { mutableStateOf(amountText(initial?.current ?: 0)) }
    var target by remember { mutableStateOf(initial?.let { amountText(it.target) }.orEmpty()) }
    var monthly by remember { mutableStateOf(amountText(initial?.monthly ?: 0)) }
    var hasDeadline by remember { mutableStateOf(initial?.deadline != null) }
    var deadline by remember { mutableStateOf(initial?.deadline?.format(datePattern).orEmpty()) }
    var archived by remember { mutableStateOf(expected?.archived ?: false) }
    var error by remember { mutableStateOf<String?>(null) }
    LaunchedEffect(name, date, current, target, monthly, hasDeadline, deadline, archived) { error = null; commands.clearError() }
    Editor(if (expected == null) "Новая цель" else "Условия цели", commands, error, close, "Сохранить цель", {
        try {
            require(name.isNotBlank()) { "Введите название цели" }
            val day = requireNotNull(dateValue(date)) { "Проверьте дату расчёта" }
            val goal = GoalPlan(day, requireNotNull(parseMinor(current)) { "Проверьте накопленную сумму" },
                requireNotNull(parseMinor(target)) { "Проверьте сумму цели" }, requireNotNull(parseMinor(monthly)) { "Проверьте ежемесячное пополнение" },
                if (hasDeadline) requireNotNull(dateValue(deadline)) { "Проверьте срок цели" } else null).also { it.validate() }
            commands.savePlan(id, name.trim(), goal, expected, archived) { saved(id) }
        } catch (failure: IllegalArgumentException) { error = failure.message ?: "Проверьте условия цели" }
    }) {
        Entry(name, { name = it.take(80) }, "Название цели", commands.busy)
        DateEntry(date, { date = it }, "Дата расчёта", commands.busy)
        Entry(current, { current = it.take(24) }, "Уже накоплено, ₽", commands.busy, KeyboardType.Decimal)
        Entry(target, { target = it.take(24) }, "Сумма цели, ₽", commands.busy, KeyboardType.Decimal)
        Entry(monthly, { monthly = it.take(24) }, "Ежемесячно, ₽", commands.busy, KeyboardType.Decimal)
        PlanSwitch("Есть срок цели", hasDeadline, commands.busy) { hasDeadline = it }
        if (hasDeadline) DateEntry(deadline, { deadline = it }, "Срок цели", commands.busy)
        if (expected != null) PlanSwitch("План в архиве", archived, commands.busy) { archived = it }
        Text("Первое пополнение — через месяц от даты расчёта. Проценты и доходность не предполагаются.")
    }
}
@Composable
private fun PlanSwitch(label: String, checked: Boolean, busy: Boolean, change: (Boolean) -> Unit) {
    Row(Modifier.fillMaxWidth(), horizontalArrangement = Arrangement.SpaceBetween) {
        Text(label, Modifier.weight(1f))
        Switch(checked, change, enabled = !busy, modifier = Modifier.semantics { contentDescription = label })
    }
}
@Composable
private fun PlanCheck(description: String, label: String, checked: Boolean, busy: Boolean, change: (Boolean) -> Unit) {
    Row(Modifier.fillMaxWidth().heightIn(min = 48.dp).toggleable(checked, enabled = !busy, role = Role.Checkbox, onValueChange = change)
        .semantics { contentDescription = description }, horizontalArrangement = Arrangement.spacedBy(12.dp)) {
        Checkbox(checked, null); Text(label, Modifier.weight(1f).padding(top = 12.dp))
    }
}
