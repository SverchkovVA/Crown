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
import ru.crown.finance.ledger.AccountBalance
import ru.crown.finance.ledger.LedgerSnapshot
import ru.crown.finance.ledger.Operation
import ru.crown.finance.ledger.OperationKind
import java.math.BigDecimal
import java.math.BigInteger
import java.text.NumberFormat
import java.time.LocalDate
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
    Accounts("Счета", R.drawable.ic_accounts),
}

@Composable
internal fun FinanceScreen(
    state: DemoState,
    retry: () -> Unit,
    bookName: String,
    leave: () -> Unit,
    commands: BookCommands? = null,
) {
    var tab by remember { mutableStateOf(Section.Operations.name) }
    var filterId by remember { mutableStateOf<String?>(null) }
    var detailsId by remember { mutableStateOf<String?>(null) }
    var editor by remember { mutableStateOf<Section?>(null) }
    val closeEditor: () -> Unit = {
        editor = null
        commands?.clearError()
    }
    val operationsScroll = rememberLazyListState()
    val accountsScroll = rememberLazyListState()
    val section = Section.valueOf(tab)
    val snapshot = (state as? DemoState.Ready)?.snapshot
    val filter = snapshot?.balances?.firstOrNull { it.account.id == filterId }
    LaunchedEffect(filterId, snapshot?.month) { operationsScroll.scrollToItem(0) }

    BackHandler(detailsId == null && editor == null) {
        if (section != Section.Operations || filterId != null) {
            tab = Section.Operations.name
            filterId = null
        } else {
            leave()
        }
    }

    Scaffold(
        topBar = { BookBar(bookName, leave) },
        bottomBar = {
            if (snapshot != null) {
                Surface(color = MaterialTheme.colorScheme.surface) {
                    Column {
                        if (commands != null) {
                            val createAccount = section == Section.Accounts || snapshot.balances.isEmpty()
                            Button(
                                onClick = {
                                    commands.clearError()
                                    editor = if (createAccount) Section.Accounts else Section.Operations
                                },
                                enabled = !commands.busy,
                                modifier = Modifier.fillMaxWidth().padding(horizontal = 20.dp, vertical = 6.dp),
                            ) {
                                Icon(painterResource(R.drawable.ic_add), contentDescription = null)
                                Spacer(Modifier.width(8.dp))
                                Text(if (createAccount) "Добавить счёт" else "Добавить операцию")
                            }
                        }
                        NavigationBar(containerColor = MaterialTheme.colorScheme.surface) {
                            for (destination in Section.entries) {
                                NavigationBarItem(
                                    selected = section == destination,
                                    onClick = {
                                        tab = destination.name
                                        filterId = null
                                    },
                                    icon = { Icon(painterResource(destination.icon), contentDescription = null) },
                                    label = { Text(destination.title) },
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
                        clearFilter = { filterId = null },
                        showDetails = { detailsId = it.id },
                        commands = commands,
                    )
                    Section.Accounts -> AccountsPage(state.snapshot, accountsScroll) {
                        filterId = it.account.id
                        tab = Section.Operations.name
                    }
                }
            }
        }
    }

    if (snapshot != null && commands != null) {
        when (editor) {
            Section.Accounts -> AccountEditor(snapshot.month, commands, closeEditor)
            Section.Operations -> OperationEditor(snapshot.balances, snapshot.month, filterId, commands, closeEditor)
            null -> Unit
        }
    }

    if (snapshot != null) {
        snapshot.operations.firstOrNull { it.id == detailsId }?.let { row ->
            OperationDetails(row, snapshot) { detailsId = null }
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
    showDetails: (Operation) -> Unit,
    commands: BookCommands?,
) {
    val rows = if (filter == null) snapshot.operations else snapshot.operations.filter {
        it.accountId == filter.account.id || it.destinationId == filter.account.id
    }
    LazyColumn(
        modifier = Modifier.fillMaxSize(),
        state = scroll,
        contentPadding = PaddingValues(start = 20.dp, end = 20.dp, bottom = 24.dp),
    ) {
        item("header") {
            PageTitle("Операции", monthLabel(snapshot.month))
            if (commands != null) {
                MonthActions(commands)
                commands.error?.let { FormError(it) }
            }
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
                    "За этот месяц операций нет",
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
private fun MonthActions(commands: BookCommands) {
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
private fun OperationDetails(operation: Operation, snapshot: LedgerSnapshot, close: () -> Unit) {
    ModalBottomSheet(onDismissRequest = close) {
        Column(
            Modifier.fillMaxWidth().verticalScroll(rememberScrollState())
                .padding(start = 24.dp, end = 24.dp, bottom = 28.dp),
            verticalArrangement = Arrangement.spacedBy(8.dp),
        ) {
            Text(operation.kind.label(), color = MaterialTheme.colorScheme.primary, style = MaterialTheme.typography.labelLarge)
            Text(operation.description, Modifier.semantics { heading() }, style = MaterialTheme.typography.headlineSmall)
            Text(
                money(operation.displayedAmount(), signed = operation.kind != OperationKind.Transfer),
                style = MaterialTheme.typography.headlineMedium,
            )
            Text(operation.date.format(DayFormat), style = MaterialTheme.typography.bodyMedium)
            Text(operation.accountCaption(snapshot), style = MaterialTheme.typography.bodyMedium)
            if (operation.feeMinor != 0L) Text("Комиссия ${money(operation.feeMinor)}")
            Button(onClick = close, modifier = Modifier.align(Alignment.End)) { Text("Готово") }
        }
    }
}

internal fun OperationKind.label(): String = when (this) {
    OperationKind.Opening -> "Начальный остаток"
    OperationKind.Income -> "Поступление"
    OperationKind.Expense -> "Расход"
    OperationKind.Transfer -> "Между своими счетами"
}

private fun Operation.accountCaption(snapshot: LedgerSnapshot): String {
    val from = snapshot.balances.first { it.account.id == accountId }.account.name
    val to = destinationId?.let { id -> snapshot.balances.first { it.account.id == id }.account.name }
    return if (to == null) from else "$from → $to"
}

private fun Operation.displayedAmount(): Long =
    if (kind == OperationKind.Transfer) amountMinor else movementFor(accountId)

private fun monthLabel(month: YearMonth): String =
    month.format(MonthFormat).replaceFirstChar { it.titlecase(Russian) }

private fun money(minor: Long, signed: Boolean = false): String = money(minor.toBigInteger(), signed)

private fun money(minor: BigInteger, signed: Boolean = false): String {
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
