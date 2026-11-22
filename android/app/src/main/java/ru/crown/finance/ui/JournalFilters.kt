package ru.crown.finance.ui

import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.runtime.*
import androidx.compose.ui.Modifier
import androidx.compose.ui.unit.dp
import ru.crown.finance.ledger.*

@Composable
internal fun JournalFilters(
    query: JournalQuery,
    snapshot: LedgerSnapshot,
    expanded: Boolean,
    expand: () -> Unit,
    change: (JournalQuery) -> Unit,
    commands: BookCommands?,
    accountScope: String?,
) {
    var selectedFilter by remember { mutableStateOf<FilterEvent?>(null) }
    var save by remember { mutableStateOf(false) }
    var update by remember { mutableStateOf(false) }
    TextButton(expand, Modifier.fillMaxWidth()) {
        Text(if (query.count == 0) "Поиск и фильтры" else "Поиск и фильтры (${query.count})")
    }
    if (expanded) {
        Column(Modifier.fillMaxWidth().padding(bottom = 12.dp), verticalArrangement = Arrangement.spacedBy(8.dp)) {
            val filters = snapshot.organization.filters.filterNot(FilterEvent::archived)
            if (filters.isNotEmpty()) {
                Choice("Сохранённый фильтр", selectedFilter?.id, filters.map(FilterEvent::id),
                    { id -> filters.first { it.id == id }.name }, true) { id ->
                    selectedFilter = filters.first { it.id == id }
                    change(requireNotNull(selectedFilter).query())
                }
            }
            Entry(query.text, { change(query.copy(text = it.take(100))) }, "Поиск по описанию, счёту или категории", false)
            Choice("Вид операции", query.kind, listOf(null) + OperationKind.entries, { it?.label() ?: "Все виды" }, true) {
                change(query.copy(kind = it))
            }
            val accounts = snapshot.balances.map { it.account }
            Choice("Счёт", query.accountId ?: accountScope, listOf(null) + accounts.map(Account::id),
                { id -> accounts.firstOrNull { it.id == id }?.name ?: "Все счета" }, true) {
                change(query.copy(accountId = it))
            }
            val categories = snapshot.categories
            if (categories.isNotEmpty()) {
                Choice("Категория", query.categoryId, listOf(null) + categories.map(Category::id), { id ->
                    val category = categories.firstOrNull { it.id == id }
                    val parent = categories.firstOrNull { it.id == category?.parentId }
                    if (category == null) "Все категории" else listOfNotNull(parent?.name, category.name).joinToString(" / ")
                }, true) { change(query.copy(categoryId = it)) }
            }
            val tags = snapshot.organization.tags
            if (tags.isNotEmpty()) {
                Choice("Тег", query.tagId, listOf(null) + tags.map(TagEvent::id), { id ->
                    tags.firstOrNull { it.id == id }?.let { it.name + if (it.archived) " · архив" else "" } ?: "Все теги"
                }, true) { change(query.copy(tagId = it)) }
            }
            if (query.count > 0 || accountScope != null) {
                TextButton({ selectedFilter = null; change(JournalQuery()) }) { Text("Сбросить поиск и фильтры") }
            }
            if (commands != null) {
                TextButton({ update = false; save = true }, enabled = !commands.busy) { Text("Сохранить фильтр") }
                if (selectedFilter != null) TextButton({ update = true; save = true }, enabled = !commands.busy) {
                    Text("Обновить выбранный фильтр")
                }
            }
        }
    }
    if (save && commands != null) {
        FilterSaveEditor(query.copy(accountId = query.accountId ?: accountScope), snapshot, selectedFilter.takeIf { update }, commands) {
            save = false
        }
    }
}
