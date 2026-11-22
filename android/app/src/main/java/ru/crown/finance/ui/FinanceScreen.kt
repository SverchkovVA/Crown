package ru.crown.finance.ui

import androidx.activity.compose.BackHandler
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.BoxWithConstraints
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.PaddingValues
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.WindowInsets
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.heightIn
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.statusBarsPadding
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.verticalScroll
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.LazyListState
import androidx.compose.foundation.lazy.items
import androidx.compose.foundation.lazy.rememberLazyListState
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material3.AlertDialog
import androidx.compose.material3.Button
import androidx.compose.material3.CircularProgressIndicator
import androidx.compose.material3.ExperimentalMaterial3Api
import androidx.compose.material3.FilterChip
import androidx.compose.material3.HorizontalDivider
import androidx.compose.material3.Icon
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.ModalBottomSheet
import androidx.compose.material3.NavigationBar
import androidx.compose.material3.NavigationBarItem
import androidx.compose.material3.NavigationBarItemDefaults
import androidx.compose.material3.OutlinedButton
import androidx.compose.material3.rememberModalBottomSheetState
import androidx.compose.material3.Scaffold
import androidx.compose.material3.Surface
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.platform.LocalDensity
import androidx.compose.ui.platform.testTag
import androidx.compose.ui.res.painterResource
import androidx.compose.ui.semantics.contentDescription
import androidx.compose.ui.semantics.heading
import androidx.compose.ui.semantics.semantics
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.style.TextAlign
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import ru.crown.finance.DemoState
import ru.crown.finance.R
import ru.crown.finance.ledger.JournalQuery
import ru.crown.finance.ledger.AccountBalance
import ru.crown.finance.ledger.AccountKind
import ru.crown.finance.ledger.LedgerSnapshot
import ru.crown.finance.ledger.Operation
import ru.crown.finance.ledger.OperationKind
import ru.crown.finance.ledger.OperationHistory
import ru.crown.finance.ledger.categoryTotals
import ru.crown.finance.ledger.fingerprint
import ru.crown.finance.ledger.closedMonths
import java.math.BigDecimal
import java.math.BigInteger
import java.text.NumberFormat
import java.time.LocalDate
import java.time.ZoneId
import java.time.YearMonth
import java.time.format.DateTimeFormatter
import java.util.Locale

private val Russian = Locale.forLanguageTag("ru-RU")
private val MonthFormat = DateTimeFormatter.ofPattern("LLLL yyyy", Russian)
private val DayFormat = DateTimeFormatter.ofPattern("d MMMM", Russian)
private val MoneyPanel = Color(0xFF124A39)
private val MoneyPanelLabel = Color(0xFFD1E9DE)

private enum class Section(val title: String, val icon: Int) {
    Operations("Операции", R.drawable.ic_operations),
    Bonuses("Бонусы", R.drawable.ic_bonus),
    Accounts("Счета", R.drawable.ic_accounts),
    Reports("Отчёты", R.drawable.ic_reports),
    History("История", R.drawable.ic_history),
    Periods("Периоды", R.drawable.ic_history),
    Reconciliations("Сверка остатков", R.drawable.ic_accounts),
    Protection("Настройки", R.drawable.ic_protection),
}

private enum class EditorSection { Account, Operation, Refund, Backup, Categories, Export, Reconciliation, Banks, Organization, OperationTags, Rules }

@Composable
internal fun FinanceScreen(
    state: DemoState,
    retry: () -> Unit,
    bookName: String,
    leave: () -> Unit,
    commands: BookCommands? = null,
    notice: String? = null,
) {
    var tab by remember { mutableStateOf(Section.Operations.name) }
    var journalQuery by remember { mutableStateOf(JournalQuery()) }
    var tagOperation by remember { mutableStateOf<Operation?>(null) }
    var detailsId by remember { mutableStateOf<String?>(null) }
    var editor by remember { mutableStateOf<EditorSection?>(null) }
    var correction by remember { mutableStateOf<Operation?>(null) }
    var refundPurchase by remember { mutableStateOf<Operation?>(null) }
    val closeDetails: () -> Unit = {
        detailsId = null
        commands?.clearError()
    }
    val closeEditor: () -> Unit = {
        editor = null
        correction = null
        refundPurchase = null
        commands?.clearError()
    }
    val operationsScroll = rememberLazyListState()
    val accountsScroll = rememberLazyListState()
    val section = Section.valueOf(tab)
    val snapshot = (state as? DemoState.Ready)?.snapshot
    val filter = snapshot?.balances?.firstOrNull { it.account.id == journalQuery.accountId }
    LaunchedEffect(journalQuery.accountId, snapshot?.month) { operationsScroll.scrollToItem(0) }

    BackHandler(detailsId == null && editor == null) {
        if (section == Section.History || section == Section.Periods) {
            tab = Section.Protection.name
        } else if (section == Section.Reconciliations) {
            tab = Section.Accounts.name
        } else if (section != Section.Operations || journalQuery.accountId != null) {
            tab = Section.Operations.name
            journalQuery = journalQuery.copy(accountId = null)
        } else {
            leave()
        }
    }

    Scaffold(
        topBar = {
            Column {
                BookBar(bookName, leave)
                if (notice != null) {
                    Box(Modifier.padding(horizontal = 20.dp, vertical = 6.dp)) { StatusText(notice) }
                }
            }
        },
        bottomBar = {
            if (snapshot != null) {
                Surface(color = MaterialTheme.colorScheme.surface) {
                    Column {
                        if (commands != null && (section == Section.Accounts || section == Section.Operations)) {
                            val createAccount = section == Section.Accounts || snapshot.balances.isEmpty()
                            Button(
                                onClick = {
                                    commands.clearError()
                                    editor = if (createAccount) EditorSection.Account else EditorSection.Operation
                                },
                                enabled = !commands.busy && (createAccount || snapshot.month !in closedMonths(snapshot.periods)),
                                modifier = Modifier.fillMaxWidth().padding(horizontal = 20.dp, vertical = 6.dp),
                            ) {
                                Icon(painterResource(R.drawable.ic_add), contentDescription = null)
                                Spacer(Modifier.width(8.dp))
                                Text(if (createAccount) "Добавить счёт" else "Добавить операцию")
                            }
                        }
                        BoxWithConstraints(Modifier.fillMaxWidth()) {
                        val compactNavigation = maxWidth < 400.dp && LocalDensity.current.fontScale > 1.2f
                        NavigationBar(containerColor = MaterialTheme.colorScheme.surface) {
                            for (destination in Section.entries) {
                                if (destination in listOf(Section.History, Section.Periods, Section.Reconciliations)) continue
                                if (commands == null && destination in listOf(Section.Bonuses, Section.Reports, Section.Protection)) continue
                                NavigationBarItem(
                                    modifier = Modifier.semantics { contentDescription = destination.title },
                                    selected = section == destination,
                                    onClick = {
                                        tab = destination.name
                                    },
                                    icon = { Icon(painterResource(destination.icon), contentDescription = null) },
                                    label = if (compactNavigation) null else ({ Text(destination.title) }),
                                    colors = NavigationBarItemDefaults.colors(
                                        indicatorColor = MaterialTheme.colorScheme.primaryContainer,
                                        selectedIconColor = MaterialTheme.colorScheme.onPrimaryContainer,
                                    ),
                                )
                            }
                        }
                        }
                    }
                }
            }
        },
        contentWindowInsets = WindowInsets(0, 0, 0, 0),
    ) { padding ->
        Box(
            Modifier.fillMaxSize().padding(padding),
            contentAlignment = Alignment.Center,
        ) {
            when (state) {
                DemoState.Loading -> CircularProgressIndicator()
                DemoState.Failed -> Column(
                    Modifier.padding(24.dp),
                    horizontalAlignment = Alignment.CenterHorizontally,
                    verticalArrangement = Arrangement.spacedBy(12.dp),
                ) {
                    Text("Не удалось открыть учебную книгу", textAlign = TextAlign.Center)
                    Button(onClick = retry) { Text("Повторить") }
                }
                is DemoState.Ready -> when (section) {
                    Section.Operations -> OperationsPage(
                        state.snapshot, filter, operationsScroll,
                        clearFilter = { journalQuery = journalQuery.copy(accountId = null) },
                        query = journalQuery,
                        changeQuery = { journalQuery = it },
                        showDetails = { detailsId = it.id },
                        commands = commands,
                    )
                    Section.Bonuses -> commands?.let { BonusPage(state.snapshot, it) }
                    Section.Accounts -> AccountsPage(
                        state.snapshot, accountsScroll,
                        reconcile = { tab = Section.Reconciliations.name }.takeIf { commands != null },
                    ) {
                        journalQuery = JournalQuery(accountId = it.account.id)
                        tab = Section.Operations.name
                    }
                    Section.History -> HistoryPage(state.snapshot.history) { detailsId = it.id }
                    Section.Periods -> commands?.let { PeriodsPage(state.snapshot, it) }
                    Section.Reconciliations -> commands?.let {
                        ReconciliationsPage(state.snapshot, it) { editor = EditorSection.Reconciliation }
                    }
                    Section.Reports -> ReportsPage(state.snapshot, commands) {
                        commands?.clearError()
                        editor = EditorSection.Export
                    }
                    Section.Protection -> ProtectionPage(
                        startBackup = {
                            commands?.clearError()
                            editor = EditorSection.Backup
                        },
                        openHistory = { tab = Section.History.name },
                        openCategories = {
                            commands?.clearError()
                            editor = EditorSection.Categories
                        },
                        inspectBackup = { commands?.inspectBackup?.invoke() },
                        openPeriods = { tab = Section.Periods.name },
                        openBanks = { editor = EditorSection.Banks },
                        openOrganization = { editor = EditorSection.Organization },
                        openRules = { editor = EditorSection.Rules },
                    )
                }
            }
        }
    }

    if (snapshot != null && commands != null) {
        when (editor) {
            EditorSection.Rules -> CategoryRulesEditor(snapshot, commands, closeEditor)
            EditorSection.Organization -> OrganizationEditor(snapshot, commands, closeEditor)
            EditorSection.OperationTags -> tagOperation?.let { OperationTagsEditor(it, snapshot, commands, closeEditor) }
            EditorSection.Account -> AccountEditor(snapshot.month, snapshot.banks, commands, closeEditor)
            EditorSection.Operation -> OperationEditor(
                snapshot.balances, snapshot.categories, snapshot.month, journalQuery.accountId, commands, closeEditor, correction,
                rules = ru.crown.finance.ledger.currentRules(snapshot.ruleEvents).map { it.rule() },
            )
            EditorSection.Refund -> refundPurchase?.let { purchase ->
                RefundEditor(purchase, snapshot, commands, closeEditor, correction)
            }
            EditorSection.Reconciliation -> ReconciliationEditor(snapshot, commands, closeEditor)
            EditorSection.Banks -> BankEditor(snapshot.banks, commands, closeEditor)
            EditorSection.Backup -> BackupForm(commands, closeEditor)
            EditorSection.Categories -> CategoryEditor(snapshot.categories, commands, closeEditor)
            EditorSection.Export -> JournalExportForm(snapshot, monthLabel(snapshot.month), commands, closeEditor)
            null -> Unit
        }
    }

    if (snapshot != null && detailsId != null) {
        val history = snapshot.history.firstOrNull { it.id == detailsId }
        val operation = history?.current?.value ?: snapshot.operations.firstOrNull { it.id == detailsId }
        if (operation != null) {
            OperationDetails(
                operation, snapshot, history, commands, closeDetails,
                editTags = { closeDetails(); tagOperation = operation; editor = EditorSection.OperationTags },
                edit = {
                    closeDetails()
                    correction = operation
                    if (operation.kind == OperationKind.Refund) {
                        refundPurchase = snapshot.history.first { it.id == operation.refundOf }.current.value
                        editor = EditorSection.Refund
                    } else {
                        editor = EditorSection.Operation
                    }
                },
                refund = {
                    closeDetails()
                    refundPurchase = operation
                    editor = EditorSection.Refund
                },
            )
        }
    }
}

@Composable
private fun BookBar(name: String, leave: () -> Unit) {
    Surface(color = MaterialTheme.colorScheme.background) {
        Row(
            Modifier.fillMaxWidth().statusBarsPadding().padding(horizontal = 20.dp, vertical = 9.dp),
            horizontalArrangement = Arrangement.spacedBy(14.dp),
            verticalAlignment = Alignment.CenterVertically,
        ) {
            Text(
                "Crown",
                style = MaterialTheme.typography.titleLarge,
                fontWeight = FontWeight.SemiBold,
                color = MaterialTheme.colorScheme.primary,
            )
            Text(
                name, Modifier.weight(1f),
                maxLines = 1, overflow = TextOverflow.Ellipsis,
                style = MaterialTheme.typography.labelLarge,
                color = MaterialTheme.colorScheme.onSurfaceVariant,
            )
            TextButton(onClick = leave) { Text("Закрыть") }
        }
    }
}

@Composable
private fun OperationsPage(
    snapshot: LedgerSnapshot,
    filter: AccountBalance?,
    scroll: LazyListState,
    clearFilter: () -> Unit,
    query: JournalQuery,
    changeQuery: (JournalQuery) -> Unit,
    showDetails: (Operation) -> Unit,
    commands: BookCommands?,
) {
    var expanded by remember { mutableStateOf(false) }
    val accountRows = if (filter == null) snapshot.operations else snapshot.operations.filter {
        it.accountId == filter.account.id || it.destinationId == filter.account.id
    }
    val rows = remember(accountRows, query, snapshot.categories, snapshot.balances, snapshot.organization) {
        query.apply(accountRows, snapshot.balances.map { it.account }, snapshot.categories, snapshot.organization.tagsByOperation())
    }
    LazyColumn(
        modifier = Modifier.fillMaxSize().testTag("operations-list"),
        state = scroll,
        contentPadding = PaddingValues(start = 20.dp, end = 20.dp, bottom = 24.dp),
    ) {
        item("header") {
            PageTitle("Операции", monthLabel(snapshot.month))
            if (commands != null) {
                MonthActions(commands)
                if (snapshot.balances.isNotEmpty()) {
                    TextButton(commands.chooseStatement, enabled = !commands.busy) { Text("Импорт выписки") }
                }
                commands.error?.let { FormError(it) }
                if (snapshot.month in closedMonths(snapshot.periods)) Text("Месяц закрыт")
            }
            JournalFilters(query, snapshot, expanded, { expanded = !expanded }, changeQuery, commands, filter?.account?.id)
            if (query.count > 0) Text("Найдено операций: ${rows.size} из ${accountRows.size}")
            if (filter != null) {
                FilterChip(
                    selected = true,
                    onClick = clearFilter,
                    label = { Text(filter.account.name) },
                    trailingIcon = {
                        Icon(painterResource(R.drawable.ic_close), contentDescription = "Сбросить фильтр")
                    },
                    modifier = Modifier.heightIn(min = 48.dp),
                )
            }
            Spacer(Modifier.height(16.dp))
            BalancePanel(
                "Остаток на ${snapshot.month.atEndOfMonth().format(DayFormat)}",
                filter?.minor ?: snapshot.totalMinor,
                snapshot.takeIf { filter == null },
            )
        }
        for ((date, dayRows) in rows.groupBy(Operation::date)) {
            item("date-$date") { DayHeading(date) }
            items(dayRows, key = { it.id }) { operation ->
                OperationItem(operation, snapshot, filter?.account?.id, showDetails)
            }
        }
        if (rows.isEmpty()) {
            item("empty") {
                Text(
                    if (query.count > 0) "По выбранным условиям операций нет" else "За этот месяц операций нет",
                    modifier = Modifier.padding(vertical = 20.dp),
                    color = MaterialTheme.colorScheme.onSurfaceVariant,
                )
            }
        }
    }
}

@Composable
private fun AccountsPage(
    snapshot: LedgerSnapshot,
    scroll: LazyListState,
    reconcile: (() -> Unit)?,
    open: (AccountBalance) -> Unit,
) {
    LazyColumn(
        modifier = Modifier.fillMaxSize(),
        state = scroll,
        contentPadding = PaddingValues(start = 20.dp, end = 20.dp, bottom = 24.dp),
    ) {
        item("header") {
            PageTitle("Счета", "Остатки на ${snapshot.month.atEndOfMonth().format(DayFormat)}")
            Spacer(Modifier.height(16.dp))
            BalancePanel("Всего на счетах", snapshot.totalMinor)
            if (reconcile != null) OutlinedButton(reconcile, Modifier.fillMaxWidth().padding(top = 8.dp)) { Text("Сверка остатков") }
            Spacer(Modifier.height(20.dp))
        }
        items(snapshot.balances, key = { it.account.id }) { balance ->
            Surface(
                onClick = { open(balance) },
                shape = RoundedCornerShape(16.dp),
                color = MaterialTheme.colorScheme.surface,
                modifier = Modifier.fillMaxWidth().padding(bottom = 8.dp),
            ) {
                Row(
                    Modifier.heightIn(min = 68.dp).padding(horizontal = 14.dp, vertical = 12.dp),
                    horizontalArrangement = Arrangement.spacedBy(12.dp),
                    verticalAlignment = Alignment.CenterVertically,
                ) {
                    Icon(painterResource(R.drawable.ic_accounts), null, tint = MaterialTheme.colorScheme.primary)
                    Column(Modifier.weight(1f), verticalArrangement = Arrangement.spacedBy(2.dp)) {
                        Text(balance.account.name, style = MaterialTheme.typography.titleMedium)
                        val bank = snapshot.banks.firstOrNull { it.id == balance.account.bankId }?.name
                        val kind = balance.account.kind.takeUnless { it == AccountKind.Other }?.title
                        val caption = listOfNotNull(bank, kind).joinToString(" · ")
                        if (caption.isNotEmpty()) Text(caption, style = MaterialTheme.typography.bodySmall)
                        Text(money(balance.minor), style = MaterialTheme.typography.bodyLarge)
                    }
                    Icon(
                        painterResource(R.drawable.ic_chevron),
                        "Показать операции счёта",
                        tint = MaterialTheme.colorScheme.onSurfaceVariant,
                    )
                }
            }
        }
    }
}

@Composable
internal fun MonthActions(commands: BookCommands) {
    Row {
        TextButton(
            onClick = { commands.changeMonth(-1) }, enabled = !commands.busy,
            modifier = Modifier.semantics { contentDescription = "Предыдущий месяц" },
        ) { Text("←") }
        TextButton(
            onClick = { commands.changeMonth(1) }, enabled = !commands.busy,
            modifier = Modifier.semantics { contentDescription = "Следующий месяц" },
        ) { Text("→") }
    }
}

@Composable
private fun PageTitle(title: String, subtitle: String) {
    Text(
        title, Modifier.semantics { heading() },
        style = MaterialTheme.typography.headlineLarge, fontWeight = FontWeight.SemiBold,
    )
    Text(subtitle, color = MaterialTheme.colorScheme.onSurfaceVariant, style = MaterialTheme.typography.bodyMedium)
}

@Composable
private fun BalancePanel(label: String, total: BigInteger, snapshot: LedgerSnapshot? = null) {
    Surface(color = MoneyPanel, contentColor = Color.White, shape = RoundedCornerShape(20.dp)) {
        Column(Modifier.fillMaxWidth().padding(18.dp)) {
            Text(label, color = MoneyPanelLabel, style = MaterialTheme.typography.labelLarge)
            Text(
                money(total), style = MaterialTheme.typography.headlineMedium,
                fontWeight = FontWeight.SemiBold, modifier = Modifier.padding(top = 3.dp),
            )
            if (snapshot != null) {
                HorizontalDivider(Modifier.padding(vertical = 12.dp), color = Color(0xFF558371))
                BoxWithConstraints(Modifier.fillMaxWidth()) {
                    if (maxWidth < 255.dp || LocalDensity.current.fontScale >= 1.35f) {
                        Column(verticalArrangement = Arrangement.spacedBy(8.dp)) {
                            PanelMetric("Поступления", snapshot.incomeMinor)
                            PanelMetric("Расходы", -snapshot.expenseMinor)
                        }
                    } else {
                        Row(horizontalArrangement = Arrangement.spacedBy(12.dp)) {
                            PanelMetric("Поступления", snapshot.incomeMinor, Modifier.weight(1f))
                            PanelMetric("Расходы", -snapshot.expenseMinor, Modifier.weight(1f))
                        }
                    }
                }
            }
        }
    }
}

@Composable
private fun PanelMetric(label: String, minor: BigInteger, modifier: Modifier = Modifier) {
    Column(modifier) {
        Text(label, color = MoneyPanelLabel, style = MaterialTheme.typography.labelMedium)
        Text(money(minor, signed = true), style = MaterialTheme.typography.titleMedium)
    }
}

@Composable
private fun DayHeading(date: LocalDate) {
    Text(
        date.format(DayFormat),
        Modifier.padding(top = 20.dp, bottom = 6.dp).semantics { heading() },
        color = MaterialTheme.colorScheme.onSurfaceVariant, style = MaterialTheme.typography.labelLarge,
    )
}

@Composable
private fun OperationItem(
    operation: Operation,
    snapshot: LedgerSnapshot,
    accountId: String?,
    open: (Operation) -> Unit,
) {
    val movement = accountId?.let(operation::movementFor) ?: operation.displayedAmount()
    val tint = when {
        operation.kind == OperationKind.Transfer -> MaterialTheme.colorScheme.secondary
        movement > 0 -> MaterialTheme.colorScheme.primary
        else -> MaterialTheme.colorScheme.onSurface
    }
    Surface(
        onClick = { open(operation) }, shape = RoundedCornerShape(12.dp),
        color = MaterialTheme.colorScheme.surface, modifier = Modifier.fillMaxWidth().padding(bottom = 4.dp),
    ) {
        BoxWithConstraints(Modifier.heightIn(min = 62.dp).padding(horizontal = 12.dp, vertical = 9.dp)) {
            val stacked = maxWidth < 270.dp || LocalDensity.current.fontScale >= 1.35f
            val amount = money(movement, signed = accountId != null || operation.kind != OperationKind.Transfer)
            if (stacked) {
                Column(verticalArrangement = Arrangement.spacedBy(4.dp)) {
                    OperationCaption(operation, snapshot)
                    Text(amount, color = tint, style = MaterialTheme.typography.titleSmall)
                }
            } else {
                Row(
                    horizontalArrangement = Arrangement.spacedBy(12.dp),
                    verticalAlignment = Alignment.CenterVertically,
                ) {
                    OperationCaption(operation, snapshot, Modifier.weight(1f))
                    Text(amount, color = tint, style = MaterialTheme.typography.titleSmall)
                }
            }
        }
    }
}

@Composable
private fun OperationCaption(operation: Operation, snapshot: LedgerSnapshot, modifier: Modifier = Modifier) {
    Column(modifier) {
        Text(operation.description, style = MaterialTheme.typography.titleSmall)
        Text(
            operation.accountCaption(snapshot),
            color = MaterialTheme.colorScheme.onSurfaceVariant, style = MaterialTheme.typography.bodySmall,
        )
    }
}

@OptIn(ExperimentalMaterial3Api::class)
@Composable
private fun OperationDetails(
    operation: Operation,
    snapshot: LedgerSnapshot,
    history: OperationHistory?,
    commands: BookCommands?,
    close: () -> Unit,
    edit: () -> Unit,
    editTags: () -> Unit,
    refund: () -> Unit,
) {
    var confirmCancellation by remember { mutableStateOf(false) }
    val cancelled = history?.current?.cancelled == true
    val locked = java.time.YearMonth.from(operation.date) in closedMonths(snapshot.periods)
    ModalBottomSheet(
        onDismissRequest = { if (commands?.busy != true) close() },
        sheetState = rememberModalBottomSheetState(skipPartiallyExpanded = true),
    ) {
        Column(
            Modifier.fillMaxWidth().padding(start = 24.dp, end = 24.dp, bottom = 16.dp),
            verticalArrangement = Arrangement.spacedBy(8.dp),
        ) {
            Column(
                Modifier.weight(1f, fill = false).verticalScroll(rememberScrollState()),
                verticalArrangement = Arrangement.spacedBy(8.dp),
            ) {
                Text(
                    operation.kind.label(),
                    color = MaterialTheme.colorScheme.primary,
                    style = MaterialTheme.typography.labelLarge,
                )
                Text(operation.description, Modifier.semantics { heading() }, style = MaterialTheme.typography.headlineSmall)
                Text(
                    money(operation.displayedAmount(), signed = operation.kind != OperationKind.Transfer),
                    style = MaterialTheme.typography.headlineMedium,
                )
                Text(operation.date.format(DayFormat), style = MaterialTheme.typography.bodyMedium)
                Text(operation.accountCaption(snapshot), style = MaterialTheme.typography.bodyMedium)
                AllocationSummary(operation, snapshot)
                val tags = snapshot.organization.tagsByOperation()[operation.id].orEmpty()
                if (tags.isNotEmpty()) Text("Теги: " + snapshot.organization.tags.filter { it.id in tags }.joinToString { it.name })
                if (operation.kind == OperationKind.Refund) {
                    val source = snapshot.history.firstOrNull { it.id == operation.refundOf }?.revisions
                        ?.firstOrNull { it.value.fingerprint() == operation.refundVersion }?.value
                    if (source != null) {
                        Text("Исходная покупка", style = MaterialTheme.typography.labelLarge)
                        Text(source.description)
                        Text("${source.date.format(DayFormat)} · ${money(source.amountMinor)}")
                        AllocationSummary(source, snapshot)
                    }
                }
                if (operation.feeMinor != 0L) Text("Комиссия ${money(operation.feeMinor)}")
                if (cancelled) FormError("Операция отменена")
                if (history != null && history.revisions.size > 1) RevisionList(history, snapshot)
            }
            if (commands != null && !cancelled) {
                OutlinedButton(editTags, Modifier.fillMaxWidth(), enabled = !commands.busy) { Text("Изменить теги") }
            }
            if (commands != null && !cancelled && operation.kind == OperationKind.Expense &&
                availableRefund(operation, snapshot).isNotEmpty()) {
                OutlinedButton(refund, Modifier.fillMaxWidth(), enabled = !commands.busy) { Text("Оформить возврат") }
            }
            if (locked && !cancelled) Text("Для исправления или отмены откройте месяц в разделе «Периоды»")
            if (commands != null && !cancelled && !locked && operation.kind != OperationKind.Opening) {
                Button(onClick = edit, enabled = !commands.busy, modifier = Modifier.fillMaxWidth()) {
                    Text("Исправить")
                }
                TextButton(
                    onClick = { confirmCancellation = true }, enabled = !commands.busy,
                    modifier = Modifier.fillMaxWidth(),
                ) { Text("Отменить операцию") }
            }
            commands?.error?.let { FormError(it) }
            TextButton(
                onClick = close, enabled = commands?.busy != true,
                modifier = Modifier.align(Alignment.End),
            ) { Text("Готово") }
        }
    }
    if (confirmCancellation && commands != null) {
        AlertDialog(
            onDismissRequest = { confirmCancellation = false },
            title = { Text("Отменить операцию?") },
            text = { Text("Остатки пересчитаются. Запись останется в истории.") },
            confirmButton = {
                TextButton(onClick = {
                    confirmCancellation = false
                    commands.reviseOperation(operation, null, close)
                }) { Text("Подтвердить отмену") }
            },
            dismissButton = {
                TextButton(onClick = { confirmCancellation = false }) { Text("Назад") }
            },
        )
    }
}

@Composable
private fun ReportsPage(snapshot: LedgerSnapshot, commands: BookCommands?, export: () -> Unit) {
    val totals = remember(snapshot) { categoryTotals(snapshot) }
    LazyColumn(contentPadding = PaddingValues(horizontal = 20.dp, vertical = 12.dp), modifier = Modifier.fillMaxSize()) {
        item {
            PageTitle("Отчёты", monthLabel(snapshot.month))
            if (commands != null) MonthActions(commands)
            Spacer(Modifier.height(12.dp))
            BalancePanel("Остаток на конец месяца", snapshot.totalMinor, snapshot)
            if (commands != null && snapshot.operations.isNotEmpty()) {
                OutlinedButton(export, Modifier.fillMaxWidth().padding(top = 8.dp), enabled = !commands.busy) {
                    Text("Экспорт журнала")
                }
            }
            Spacer(Modifier.height(12.dp))
            Text("По категориям", Modifier.semantics { heading() }, style = MaterialTheme.typography.titleLarge)
        }
        if (totals.isEmpty()) item { Text("За этот месяц поступлений и расходов нет") }
        for ((kind, group) in totals.groupBy { it.kind }) {
            item {
                Text(
                    if (kind == OperationKind.Income) "Поступления" else "Расходы",
                    Modifier.padding(top = 12.dp, bottom = 4.dp).semantics { heading() },
                    color = MaterialTheme.colorScheme.onSurfaceVariant,
                    style = MaterialTheme.typography.labelLarge,
                )
            }
            items(group) { row ->
                Column(Modifier.fillMaxWidth().padding(vertical = 8.dp)) {
                    Text(row.name, style = MaterialTheme.typography.titleSmall)
                    Text(money(row.minor), style = MaterialTheme.typography.bodyLarge)
                }
                HorizontalDivider()
            }
        }
    }
}

@Composable
private fun HistoryPage(history: List<OperationHistory>, showDetails: (OperationHistory) -> Unit) {
    val changed = history.filter { it.revisions.size > 1 }
        .sortedByDescending { it.current.recordedAt }
    LazyColumn(contentPadding = PaddingValues(horizontal = 20.dp, vertical = 12.dp), modifier = Modifier.fillMaxSize()) {
        item {
            PageTitle("История", "Исправления и отмены")
            Spacer(Modifier.height(16.dp))
        }
        if (changed.isEmpty()) item { Text("Изменений пока нет") }
        items(changed, key = { it.id }) { item ->
            val value = item.current.value
            Surface(
                onClick = { showDetails(item) },
                color = MaterialTheme.colorScheme.surface, shape = MaterialTheme.shapes.medium,
                modifier = Modifier.fillMaxWidth().padding(bottom = 6.dp),
            ) {
                Column(Modifier.padding(14.dp), verticalArrangement = Arrangement.spacedBy(2.dp)) {
                    Text(value.description, style = MaterialTheme.typography.titleSmall)
                    Text(if (item.current.cancelled) "Отменена" else "Исправлена")
                }
            }
        }
    }
}

@Composable
private fun AllocationSummary(operation: Operation, snapshot: LedgerSnapshot) {
    if (operation.parts.isEmpty()) {
        snapshot.categories.firstOrNull { it.id == operation.categoryId }?.let { Text(it.name) }
    } else {
        Text("По категориям", style = MaterialTheme.typography.labelLarge)
        for (part in operation.parts) {
            val name = snapshot.categories.firstOrNull { it.id == part.categoryId }?.name ?: "Без категории"
            Text("$name · ${money(part.amountMinor)}", style = MaterialTheme.typography.bodyMedium)
        }
    }
}

@Composable
private fun RevisionList(history: OperationHistory, snapshot: LedgerSnapshot) {
    val stamp = DateTimeFormatter.ofPattern("dd.MM.uuuu HH:mm", Russian).withZone(ZoneId.systemDefault())
    HorizontalDivider(Modifier.padding(vertical = 4.dp))
    Text("Версии операции", Modifier.semantics { heading() }, style = MaterialTheme.typography.titleMedium)
    for (revision in history.revisions.asReversed()) {
        val value = revision.value
        val label = when {
            revision.cancelled -> "Отмена"
            revision.recordedAt == null -> "Исходная версия"
            else -> "Исправление"
        }
        val time = revision.recordedAt?.let(stamp::format)
        Text(listOfNotNull(label, time).joinToString(" · "), style = MaterialTheme.typography.labelMedium)
        Text(value.description)
        AllocationSummary(value, snapshot)
        Text("${value.date.format(DayFormat)} · ${money(value.displayedAmount())} · ${value.accountCaption(snapshot)}")
        if (value.feeMinor != 0L) Text("Комиссия ${money(value.feeMinor)}")
    }
    HorizontalDivider(Modifier.padding(vertical = 4.dp))
}

internal fun OperationKind.label(): String = when (this) {
    OperationKind.Opening -> "Начальный остаток"
    OperationKind.Income -> "Поступление"
    OperationKind.Expense -> "Расход"
    OperationKind.Transfer -> "Между своими счетами"
    OperationKind.Refund -> "Возврат"
}

private fun Operation.accountCaption(snapshot: LedgerSnapshot): String {
    val from = snapshot.balances.first { it.account.id == accountId }.account.name
    val to = destinationId?.let { id -> snapshot.balances.first { it.account.id == id }.account.name }
    return if (to == null) from else "$from → $to"
}

private fun Operation.displayedAmount(): Long =
    if (kind == OperationKind.Transfer) amountMinor else movementFor(accountId)

internal fun monthLabel(month: YearMonth): String =
    month.format(MonthFormat).replaceFirstChar { it.titlecase(Russian) }

internal fun money(minor: Long, signed: Boolean = false): String = money(minor.toBigInteger(), signed)

internal fun money(minor: BigInteger, signed: Boolean = false): String {
    val formatter = NumberFormat.getNumberInstance(Russian).apply {
        minimumFractionDigits = 2
        maximumFractionDigits = 2
    }
    val sign = when {
        minor.signum() < 0 -> "−"
        signed && minor.signum() > 0 -> "+"
        else -> ""
    }
    return sign + formatter.format(BigDecimal(minor, 2).abs()) + "\u00A0₽"
}
