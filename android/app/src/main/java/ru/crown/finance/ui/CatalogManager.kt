package ru.crown.finance.ui

import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.*
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.material3.*
import androidx.compose.runtime.*
import androidx.compose.ui.Modifier
import androidx.compose.ui.res.painterResource
import androidx.compose.ui.semantics.contentDescription
import androidx.compose.ui.semantics.semantics
import androidx.compose.ui.unit.dp
import ru.crown.finance.R
import ru.crown.finance.ledger.*
import java.time.Instant
import java.time.ZoneId
import java.time.format.DateTimeFormatter

@OptIn(ExperimentalMaterial3Api::class)
@Composable
internal fun CatalogManager(snapshot: LedgerSnapshot, type: CatalogType, commands: BookCommands, close: () -> Unit) {
    var selected by remember { mutableStateOf<CatalogVersion?>(null) }
    var creating by remember { mutableStateOf(false) }
    var showArchived by remember { mutableStateOf(false) }
    val values = when (type) {
        CatalogType.Bank -> snapshot.banks.map { snapshot.catalogVersion(type, it.id) }
        CatalogType.Account -> snapshot.balances.map { snapshot.catalogVersion(type, it.account.id) }
        CatalogType.Category -> snapshot.categories.map { snapshot.catalogVersion(type, it.id) }
    }
    val back = { selected = null; creating = false; commands.clearError() }
    val currentSelection = selected
    if (currentSelection != null) {
        CatalogEditForm(snapshot, currentSelection, commands, back)
    } else if (creating) {
        when (type) {
            CatalogType.Bank -> BankEditor(emptyList(), commands, back)
            CatalogType.Account -> AccountEditor(snapshot.month, snapshot.banks.filterNot { snapshot.catalogArchived(CatalogType.Bank, it.id) }, commands, back)
            CatalogType.Category -> CategoryEditor(snapshot.categories, commands, back, selectable = { !snapshot.catalogArchived(CatalogType.Category, it) }, showExisting = false)
        }
    } else {
        ModalBottomSheet(onDismissRequest = { if (!commands.busy) close() }, sheetState = rememberModalBottomSheetState(skipPartiallyExpanded = true)) {
            Column(Modifier.fillMaxWidth().fillMaxHeight(0.92f).padding(horizontal = 20.dp, vertical = 12.dp), verticalArrangement = Arrangement.spacedBy(8.dp)) {
                Row {
                    IconButton(close, enabled = !commands.busy) { Icon(painterResource(R.drawable.ic_back), "Назад") }
                    WorkspaceTitle(when (type) { CatalogType.Bank -> "Банки"; CatalogType.Account -> "Счета"; CatalogType.Category -> "Категории" })
                }
                Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                    Checkbox(showArchived, { showArchived = it }, modifier = Modifier.semantics { contentDescription = "Показывать архив" })
                    Text("Показывать архив", Modifier.padding(top = 12.dp))
                }
                val visible = values.filter { showArchived || !it.value.archived }.sortedBy { it.value.name.lowercase() }
                LazyColumn(Modifier.weight(1f)) {
                    if (visible.isEmpty()) item { Text("Записей пока нет", Modifier.padding(vertical = 20.dp)) }
                    items(visible, key = { it.value.id }) { row ->
                        val value = row.value
                        ListItem(
                            headlineContent = { Text(value.name) },
                            supportingContent = {
                                Text(if (value.archived) "В архиве" else when (type) {
                                    CatalogType.Bank -> "Банк"
                                    CatalogType.Account -> AccountKind.fromWireName(requireNotNull(value.kind)).title
                                    CatalogType.Category -> {
                                        val kind = OperationKind.fromWireName(requireNotNull(value.kind)).label()
                                        kind + (value.parentId?.let { id -> " · " + snapshot.categories.single { it.id == id }.name } ?: "")
                                    }
                                })
                            },
                            trailingContent = { Icon(painterResource(R.drawable.ic_chevron), null) },
                            modifier = Modifier.clickable(enabled = !commands.busy) { selected = row; commands.clearError() }
                                .semantics { contentDescription = "Изменить: " + value.name },
                        )
                        HorizontalDivider()
                    }
                }
                Button({ creating = true; commands.clearError() }, Modifier.fillMaxWidth(), enabled = !commands.busy) {
                    Text(when (type) { CatalogType.Bank -> "Новый банк"; CatalogType.Account -> "Новый счёт"; CatalogType.Category -> "Новая категория" })
                }
            }
        }
    }
}

@Composable
internal fun CatalogEditForm(snapshot: LedgerSnapshot, expected: CatalogVersion, commands: BookCommands, close: () -> Unit) {
    val initial = expected.value
    var name by remember { mutableStateOf(initial.name) }
    var bank by remember { mutableStateOf(initial.bankId) }
    var parent by remember { mutableStateOf(initial.parentId) }
    var kind by remember { mutableStateOf(initial.kind) }
    var archived by remember { mutableStateOf(initial.archived) }
    var history by remember { mutableStateOf(false) }
    var page by remember { mutableIntStateOf(0) }
    var error by remember { mutableStateOf<String?>(null) }
    fun clear() { error = null; commands.clearError() }
    Editor(when (initial.type) { CatalogType.Bank -> "Изменить банк"; CatalogType.Account -> "Изменить счёт"; CatalogType.Category -> "Изменить категорию" },
        commands, error, close, "Сохранить изменения", {
            try {
                val value = initial.copy(name = name.trim(), bankId = bank, parentId = parent, kind = kind, archived = archived)
                value.validate()
                commands.saveCatalog(expected, value, close)
            } catch (failure: IllegalArgumentException) { error = failure.message ?: "Проверьте поля справочника" }
        }
    ) {
        Entry(name, { name = it.take(if (initial.type == CatalogType.Category) 80 else 100); clear() }, "Название", commands.busy)
        if (initial.type == CatalogType.Account) {
            Choice("Вид счёта", AccountKind.fromWireName(requireNotNull(kind)), AccountKind.entries, AccountKind::title, !commands.busy) { kind = it.wireName; clear() }
            val banks = snapshot.banks.filter { !snapshot.catalogArchived(CatalogType.Bank, it.id) || it.id == initial.bankId }
            Choice("Банк", bank, listOf(null) + banks.map(Bank::id), { id ->
                snapshot.banks.firstOrNull { it.id == id }?.let { it.name + if (snapshot.catalogArchived(CatalogType.Bank, it.id)) " · архив" else "" } ?: "Не указан"
            }, !commands.busy) { bank = it; clear() }
        }
        if (initial.type == CatalogType.Category) {
            Text("Вид: " + OperationKind.fromWireName(requireNotNull(kind)).label())
            fun descendant(id: String): Boolean {
                var current: String? = id
                val visited = mutableSetOf<String>()
                while (current != null && visited.add(current)) {
                    if (current == initial.id) return true
                    current = snapshot.categories.single { it.id == current }.parentId
                }
                return false
            }
            val parents = snapshot.categories.filter { it.kind.wireName == kind && !descendant(it.id) &&
                (!snapshot.catalogArchived(CatalogType.Category, it.id) || it.id == initial.parentId) }
            Choice("Родительская категория", parent, listOf(null) + parents.map(Category::id), { id ->
                snapshot.categories.firstOrNull { it.id == id }?.let { it.name + if (snapshot.catalogArchived(CatalogType.Category, it.id)) " · архив" else "" } ?: "Без родительской категории"
            }, !commands.busy) { parent = it; clear() }
        }
        Row(Modifier.fillMaxWidth(), horizontalArrangement = Arrangement.SpaceBetween) {
            Text("В архиве", Modifier.weight(1f).padding(top = 12.dp))
            Switch(archived, { archived = it; clear() }, enabled = !commands.busy, modifier = Modifier.semantics { contentDescription = "В архиве" })
        }
        if (archived) Text("Новые назначения недоступны. Прежние записи, отчёты и остатки сохраняются.")
        TextButton({ history = !history }) { Text(if (history) "Скрыть историю записи" else "История записи") }
        if (history) {
            val versions = snapshot.catalogHistory.filter { it.value.key == initial.key }.ifEmpty { listOf(expected) }.asReversed()
            val pages = (versions.size + 19) / 20
            Text("Версий: " + versions.size)
            versions.drop(page * 20).take(20).forEach { version ->
                val value = version.value
                HorizontalDivider()
                Text(if (version.sequence == 0L) "Исходные данные · дата не сохранялась" else
                    Instant.ofEpochMilli(version.recordedAt).atZone(ZoneId.systemDefault()).format(DateTimeFormatter.ofPattern("dd.MM.uuuu HH:mm")))
                Text(value.name + if (value.archived) " · архив" else "")
                value.bankId?.let { id -> Text("Банк: " + snapshot.banks.single { it.id == id }.name) }
                value.parentId?.let { id -> Text("Родитель: " + snapshot.categories.single { it.id == id }.name) }
                if (value.type == CatalogType.Account) Text(AccountKind.fromWireName(requireNotNull(value.kind)).title)
            }
            if (pages > 1) Row {
                TextButton({ page-- }, enabled = page > 0) { Text("Новее") }
                TextButton({ page++ }, enabled = page + 1 < pages) { Text("Старее") }
            }
        }
    }
}
