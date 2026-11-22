package ru.crown.finance.ui

import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.material3.Checkbox
import androidx.compose.material3.Switch
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.runtime.*
import androidx.compose.ui.Modifier
import androidx.compose.ui.semantics.contentDescription
import androidx.compose.ui.semantics.semantics
import ru.crown.finance.ledger.*

@Composable
internal fun OrganizationEditor(snapshot: LedgerSnapshot, commands: BookCommands, close: () -> Unit) {
    var mode by remember { mutableStateOf("Теги") }
    var tag by remember { mutableStateOf<TagEvent?>(null) }
    var filter by remember { mutableStateOf<FilterEvent?>(null) }
    var name by remember { mutableStateOf("") }
    var archived by remember { mutableStateOf(false) }
    var error by remember { mutableStateOf<String?>(null) }
    LaunchedEffect(name, archived, tag, filter) { error = null; commands.clearError() }
    fun reset() { tag = null; filter = null; name = ""; archived = false }
    Editor("Теги и фильтры", commands, error, close,
        if (mode == "Теги" && tag == null) "Создать тег" else "Сохранить изменения",
        submit = {
            if (name.isBlank()) error = "Введите название" else if (mode == "Теги") {
                commands.saveTag(name, tag, archived) { reset() }
            } else if (filter != null) {
                commands.saveFilter(name, requireNotNull(filter).query(), filter, archived) { reset() }
            } else error = "Выберите сохранённый фильтр"
        },
    ) {
        Choice("Раздел", mode, listOf("Теги", "Сохранённые фильтры"), { it }, !commands.busy) {
            mode = it; reset()
        }
        if (mode == "Теги" || filter != null) {
            Entry(name, { name = it.take(80) }, if (mode == "Теги") "Название тега" else "Название фильтра", commands.busy)
            if (tag != null || filter != null) {
                Row(Modifier.fillMaxWidth(), horizontalArrangement = Arrangement.SpaceBetween) {
                    Text("В архиве", Modifier.weight(1f))
                    Switch(archived, { archived = it }, enabled = !commands.busy,
                        modifier = Modifier.semantics { contentDescription = "В архиве" })
                }
            }
        }
        if (mode == "Теги") {
            TextButton({ reset() }, enabled = !commands.busy) { Text("Новый тег") }
            if (snapshot.organization.tags.isEmpty()) Text("Создайте тег, затем назначьте его в карточке операции")
            for (item in snapshot.organization.tags) {
                TextButton({
                    tag = item; filter = null; name = item.name; archived = item.archived
                }, enabled = !commands.busy) { Text(item.name + if (item.archived) " · архив" else "") }
            }
            tag?.let { selected ->
                val versions = snapshot.organization.tagEvents.filter { it.id == selected.id }
                Text("Версий тега: ${versions.size}")
                for (version in versions) Text(version.name + if (version.archived) " · архив" else " · действующий")
            }
        } else {
            Text("Условия создаются в журнале: «Поиск и фильтры → Сохранить фильтр»")
            for (item in snapshot.organization.filters) {
                TextButton({
                    filter = item; tag = null; name = item.name; archived = item.archived
                }, enabled = !commands.busy) { Text(item.name + if (item.archived) " · архив" else "") }
            }
            filter?.let { selected ->
                Text("Условий: ${selected.query().count}. Применяются к выбранному месяцу.")
                Text("Версий фильтра: ${snapshot.organization.filterEvents.count { it.id == selected.id }}")
            }
        }
    }
}

@Composable
internal fun OperationTagsEditor(operation: Operation, snapshot: LedgerSnapshot, commands: BookCommands, close: () -> Unit) {
    val expected = remember(operation.id) { snapshot.organization.tagsByOperation()[operation.id].orEmpty() }
    var selected by remember(operation.id) { mutableStateOf(expected) }
    LaunchedEffect(selected) { commands.clearError() }
    Editor("Теги операции", commands, null, close, "Сохранить теги", submit = {
        commands.assignTags(operation.id, expected, selected, close)
    }) {
        Text(operation.description)
        Text("Теги не меняют сумму, остатки и исходную версию операции")
        for (tag in snapshot.organization.tags.filter { !it.archived || it.id in expected }) {
            Row(Modifier.fillMaxWidth()) {
                Checkbox(tag.id in selected, { checked -> selected = if (checked) selected + tag.id else selected - tag.id },
                    enabled = !commands.busy,
                    modifier = Modifier.semantics { contentDescription = "Тег: ${tag.name}" })
                Text(tag.name + if (tag.archived) " · архив" else "", Modifier.weight(1f))
            }
        }
        if (snapshot.organization.tags.isEmpty()) Text("Сначала создайте тег в настройках")
    }
}

@Composable
internal fun FilterSaveEditor(query: JournalQuery, snapshot: LedgerSnapshot, expected: FilterEvent?, commands: BookCommands, close: () -> Unit) {
    var name by remember { mutableStateOf(expected?.name.orEmpty()) }
    var error by remember { mutableStateOf<String?>(null) }
    LaunchedEffect(name) { error = null; commands.clearError() }
    Editor("Сохранить фильтр", commands, error, close, "Сохранить фильтр", submit = {
        if (name.isBlank()) error = "Введите название фильтра"
        else commands.saveFilter(name, query, expected, false, close)
    }) {
        Entry(name, { name = it.take(80) }, "Название фильтра", commands.busy)
        Text("Фильтр применяется к выбранному месяцу")
        if (query.text.isNotBlank()) Text("Текст: ${query.text}")
        query.kind?.let { Text("Вид операции: ${it.label()}") }
        query.accountId?.let { id -> Text("Счёт: " + snapshot.balances.first { it.account.id == id }.account.name) }
        query.categoryId?.let { id -> Text("Категория: " + snapshot.categories.first { it.id == id }.name) }
        query.tagId?.let { id -> Text("Тег: " + snapshot.organization.tags.first { it.id == id }.name) }
        Text("Сохраняются условия поиска. Операции и остатки не изменяются.")
    }
}
