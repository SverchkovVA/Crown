package ru.crown.finance.ui

import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.material3.OutlinedButton
import androidx.compose.material3.Switch
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.runtime.*
import androidx.compose.ui.Modifier
import androidx.compose.ui.semantics.contentDescription
import androidx.compose.ui.semantics.semantics
import androidx.compose.ui.text.input.KeyboardType
import ru.crown.finance.ledger.*
import java.util.UUID

@Composable
internal fun CategoryRulesEditor(snapshot: LedgerSnapshot, commands: BookCommands, close: () -> Unit) {
    var selected by remember { mutableStateOf<CategoryRuleEvent?>(null) }
    var id by remember { mutableStateOf(UUID.randomUUID().toString()) }
    var name by remember { mutableStateOf("") }
    var contains by remember { mutableStateOf("") }
    var priority by remember { mutableStateOf("0") }
    var kind by remember { mutableStateOf(OperationKind.Expense) }
    var account by remember { mutableStateOf<String?>(null) }
    var category by remember { mutableStateOf<String?>(null) }
    var enabled by remember { mutableStateOf(true) }
    var error by remember { mutableStateOf<String?>(null) }
    var showHistory by remember { mutableStateOf(false) }
    LaunchedEffect(name, contains, priority, kind, account, category, enabled) { error = null; commands.clearError() }
    fun reset() {
        selected = null; id = UUID.randomUUID().toString(); name = ""; contains = ""; priority = "0"
        kind = OperationKind.Expense; account = null; category = null; enabled = true; showHistory = false
    }
    Editor("Правила категорий", commands, error, close, if (selected == null) "Создать правило" else "Сохранить правило", submit = {
        val number = priority.toIntOrNull()
        error = when {
            name.isBlank() -> "Введите название правила"
            contains.isBlank() || contains.any(Char::isISOControl) -> "Введите печатный текст для поиска"
            number == null || number !in 0..999 -> "Приоритет — целое число от 0 до 999"
            category == null -> "Выберите категорию"
            else -> null
        }
        if (error == null && number != null && category != null) {
            commands.saveRule(CategoryRule(id, name.trim(), kind, contains.trim(), requireNotNull(category), number, account, enabled), selected) { reset() }
        }
    }) {
        Entry(name, { name = it.take(80) }, "Название правила", commands.busy)
        Entry(contains, { contains = it.take(200) }, "Описание содержит", commands.busy)
        Choice("Вид операции", kind, categoryKinds, OperationKind::label, !commands.busy) { kind = it; category = null }
        val categories = snapshot.categories.filter { it.kind == kind && (!snapshot.catalogArchived(CatalogType.Category, it.id) || it.id == selected?.categoryId) }
        if (categories.isEmpty()) Text("Сначала создайте категорию этого вида в настройках")
        else Choice("Категория правила", category, categories.map(Category::id),
            { id -> categories.first { it.id == id }.name }, !commands.busy) { category = it }
        val accounts = snapshot.balances.map { it.account }.filter { !snapshot.catalogArchived(CatalogType.Account, it.id) || it.id == selected?.accountId }
        Choice("Счёт правила", account, listOf(null) + accounts.map(Account::id),
            { id -> accounts.firstOrNull { it.id == id }?.name ?: "Все счета" }, !commands.busy) { account = it }
        Entry(priority, { priority = it.take(4) }, "Приоритет · 0–999", commands.busy, KeyboardType.Number)
        Text("Большее число — выше приоритет. Разные категории при равном приоритете требуют выбора.")
        Row(Modifier.fillMaxWidth(), horizontalArrangement = Arrangement.SpaceBetween) {
            Text("Включено", Modifier.weight(1f))
            Switch(enabled, { enabled = it }, enabled = !commands.busy,
                modifier = Modifier.semantics { contentDescription = "Правило включено" })
        }
        TextButton({ reset() }, enabled = !commands.busy) { Text("Новое правило") }
        for (event in currentRules(snapshot.ruleEvents).sortedByDescending(CategoryRuleEvent::priority)) {
            TextButton({
                selected = event; id = event.id; name = event.name; contains = event.containsText
                priority = event.priority.toString(); kind = event.rule().kind; account = event.accountId
                category = event.categoryId; enabled = event.enabled; showHistory = false
            }, enabled = !commands.busy) { Text(event.name + if (event.enabled) " · ${event.priority}" else " · выключено") }
        }
        selected?.let { selected ->
            TextButton({ showHistory = !showHistory }) { Text("История правила") }
            if (showHistory) for (event in snapshot.ruleEvents.filter { it.id == selected.id }) {
                Text(event.name + " · ${event.priority}" + if (event.enabled) " · включено" else " · выключено")
                Text("Содержит: ${event.containsText}")
            }
        }
    }
}

@Composable
internal fun CategoryRuleHint(suggestion: CategorySuggestion, rules: List<CategoryRule>, categories: List<Category>, enabled: Boolean, apply: (String) -> Unit) {
    when (suggestion.status) {
        SuggestionStatus.Suggested -> {
            val category = categories.first { it.id == suggestion.categoryId }
            Text("Предложена категория: ${category.name}")
            Text("По правилам: " + rules.filter { it.id in suggestion.ruleIds }.joinToString { it.name })
            OutlinedButton({ apply(requireNotNull(suggestion.categoryId)) }, Modifier.fillMaxWidth(), enabled = enabled) {
                Text("Применить категорию")
            }
        }
        SuggestionStatus.Conflict -> Text("Правила предлагают разные категории. Выберите категорию вручную.")
        else -> Unit
    }
}
