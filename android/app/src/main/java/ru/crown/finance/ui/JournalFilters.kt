package ru.crown.finance.ui

import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.runtime.Composable
import androidx.compose.ui.Modifier
import androidx.compose.ui.unit.dp
import ru.crown.finance.ledger.Category
import ru.crown.finance.ledger.JournalQuery
import ru.crown.finance.ledger.OperationKind

@Composable
internal fun JournalFilters(
    query: JournalQuery,
    categories: List<Category>,
    expanded: Boolean,
    expand: () -> Unit,
    change: (JournalQuery) -> Unit,
) {
    TextButton(expand, Modifier.fillMaxWidth()) {
        Text(if (query.count == 0) "Поиск и фильтры" else "Поиск и фильтры (${query.count})")
    }
    if (expanded) {
        Column(Modifier.fillMaxWidth().padding(bottom = 12.dp), verticalArrangement = Arrangement.spacedBy(8.dp)) {
            Entry(query.text, { change(query.copy(text = it.take(100))) }, "Поиск по описанию, счёту или категории", false)
            Choice(
                "Вид операции", query.kind, listOf(null) + OperationKind.entries,
                { it?.label() ?: "Все виды" }, true,
            ) { change(query.copy(kind = it)) }
            if (categories.isNotEmpty()) {
                Choice(
                    "Категория", query.categoryId, listOf(null) + categories.map(Category::id),
                    { id ->
                        val category = categories.firstOrNull { it.id == id }
                        if (category == null) "Все категории" else {
                            val parent = categories.firstOrNull { it.id == category.parentId }
                            listOfNotNull(parent?.name, category.name).joinToString(" / ")
                        }
                    }, true,
                ) { change(query.copy(categoryId = it)) }
            }
            if (query.count > 0) TextButton({ change(JournalQuery()) }) { Text("Сбросить поиск и фильтры") }
        }
    }
}
