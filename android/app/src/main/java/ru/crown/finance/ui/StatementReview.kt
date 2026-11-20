package ru.crown.finance.ui

import androidx.activity.compose.BackHandler
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.PaddingValues
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.navigationBarsPadding
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.statusBarsPadding
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.material3.Button
import androidx.compose.material3.Checkbox
import androidx.compose.material3.HorizontalDivider
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Scaffold
import androidx.compose.material3.Surface
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.testTag
import androidx.compose.ui.semantics.contentDescription
import androidx.compose.ui.semantics.semantics
import androidx.compose.ui.unit.dp
import ru.crown.finance.ledger.*

@Composable
internal fun StatementReview(
    document: StatementDocument,
    snapshot: LedgerSnapshot,
    commands: BookCommands,
    close: () -> Unit,
) {
    var account by remember { mutableStateOf(snapshot.balances.firstOrNull()?.account?.id) }
    val candidates = remember(document, snapshot, account) {
        account?.let { previewStatement(document, snapshot, it) }.orEmpty()
    }
    var choices by remember(document, account) {
        mutableStateOf(candidates.filter { it.status == ImportStatus.New && !it.row.needsReview }.associate { candidate ->
            val target = requireNotNull(account)
            candidate.row.position to ImportChoice(candidate.row.position, candidate.row.proposal(target, document.key(target, candidate.row)))
        })
    }
    var reviewedChoices by remember(document, account) { mutableStateOf<Map<Int, ImportChoice>>(emptyMap()) }
    var editing by remember(document, account) { mutableStateOf<StatementCandidate?>(null) }
    var confirm by remember { mutableStateOf(false) }
    var refundCandidate by remember(document, account) { mutableStateOf<StatementCandidate?>(null) }
    var refundSource by remember(document, account) { mutableStateOf<Operation?>(null) }
    BackHandler(editing == null && refundCandidate == null && !commands.busy) { close() }
    val ready = choices.isNotEmpty() && choices.values.all { choice ->
        val row = candidates.first { it.row.position == choice.position }
        (choice.operation.accountId == account || choice.operation.destinationId == account) &&
            (choice.linkExisting || choice.reviewed || (row.status == ImportStatus.New && !row.row.needsReview))
    }
    Scaffold(
        topBar = {
            Column(Modifier.fillMaxWidth().statusBarsPadding().padding(horizontal = 20.dp, vertical = 8.dp)) {
                TextButton(close, enabled = !commands.busy) { Text("← Назад") }
                WorkspaceTitle("Импорт выписки")
            }
        },
        bottomBar = {
            Surface {
                Column(
                    Modifier.fillMaxWidth().navigationBarsPadding().padding(horizontal = 20.dp, vertical = 8.dp),
                    verticalArrangement = Arrangement.spacedBy(4.dp),
                ) {
                    commands.error?.let { FormError(it) }
                    if (choices.isNotEmpty() && !ready) Text("Проверьте возможные повторы, переводы и возвраты")
                    Row(verticalAlignment = Alignment.CenterVertically) {
                        Checkbox(
                            confirm, { confirm = it }, enabled = !commands.busy,
                            modifier = Modifier.semantics { contentDescription = "Подтвердить строки выписки" },
                        )
                        Text("Выбранные строки сверены", Modifier.weight(1f))
                    }
                    Button(
                        onClick = { commands.importStatement(requireNotNull(account), choices.values.toList()) },
                        enabled = ready && confirm && !commands.busy,
                        modifier = Modifier.fillMaxWidth(),
                    ) { Text("Добавить операции (${choices.size})") }
                }
            }
        },
    ) { padding ->
        LazyColumn(
            Modifier.fillMaxSize().padding(padding).testTag("statement-rows"),
            contentPadding = PaddingValues(horizontal = 20.dp, vertical = 8.dp),
            verticalArrangement = Arrangement.spacedBy(8.dp),
        ) {
            item {
                Text("${document.kind} · предложено ${candidates.size}, исключено ${document.issues.size}")
                if (account == null) Text("Сначала создайте счёт в книге")
                else Choice(
                    "Счёт выписки", account, snapshot.balances.map { it.account.id },
                    { id -> snapshot.balances.first { it.account.id == id }.account.name }, !commands.busy,
                ) {
                    account = it
                    confirm = false
                    commands.clearError()
                }
            }
            items(candidates, key = { it.row.position }) { candidate ->
                val row = candidate.row
                val valid = candidate.status in setOf(ImportStatus.New, ImportStatus.PossibleDuplicate)
                Column(Modifier.fillMaxWidth().padding(vertical = 4.dp), verticalArrangement = Arrangement.spacedBy(4.dp)) {
                    Row(verticalAlignment = Alignment.CenterVertically) {
                        Checkbox(
                            choices.containsKey(row.position),
                            onCheckedChange = { selected ->
                                val target = requireNotNull(account)
                                choices = if (selected) choices + (row.position to (
                                    reviewedChoices[row.position] ?: ImportChoice(row.position, row.proposal(target, document.key(target, row)))
                                )) else choices - row.position
                                confirm = false
                                commands.clearError()
                            },
                            enabled = valid && !commands.busy,
                        )
                        Column(Modifier.weight(1f)) {
                            Text("${row.position}. ${row.description}", style = MaterialTheme.typography.titleSmall)
                            Text("${row.date.format(datePattern)} · ${money(row.signedMinor, signed = true)}")
                            Text(candidate.status.title, color = MaterialTheme.colorScheme.onSurfaceVariant)
                        }
                    }
                    val chosen = choices[row.position] ?: reviewedChoices[row.position]
                    if (chosen != null && (chosen.reviewed || chosen.linkExisting)) {
                        Text(
                            if (chosen.linkExisting) "Связана с записанной операцией" else "К записи · ${chosen.operation.kind.label()}",
                            style = MaterialTheme.typography.labelMedium,
                        )
                        val prepared = chosen.operation
                        val target = account
                        if (target == prepared.accountId || target == prepared.destinationId) {
                            Text("${prepared.date.format(datePattern)} · ${money(prepared.movementFor(requireNotNull(target)), signed = true)}")
                        } else {
                            FormError("Выберите счёт выписки в подготовленной операции")
                        }
                        if (prepared.description != row.description) Text(prepared.description)
                    }
                    if (valid) {
                        TextButton({
                            val refund = chosen?.takeUnless { it.linkExisting }?.operation
                                ?.takeIf { it.kind == OperationKind.Refund }
                            if (refund != null) {
                                refundCandidate = candidate
                                refundSource = snapshot.history.firstOrNull { it.id == refund.refundOf }?.current?.value
                            } else {
                                editing = candidate
                            }
                            commands.clearError()
                        }, enabled = !commands.busy) { Text("Настроить строку ${row.position}") }
                        if (row.signedMinor > 0) {
                            TextButton({
                                refundCandidate = candidate
                                refundSource = null
                                commands.clearError()
                            }, enabled = !commands.busy) { Text("Оформить возврат строки ${row.position}") }
                        }
                        if (candidate.matching.isNotEmpty()) {
                            Choice(
                                "Записанная операция", chosen?.operation?.id.takeIf { chosen?.linkExisting == true },
                                candidate.matching.map(Operation::id),
                                { id -> candidate.matching.first { it.id == id }.description }, !commands.busy,
                            ) { id ->
                                val value = ImportChoice(row.position, candidate.matching.first { it.id == id }, linkExisting = true, reviewed = true)
                                reviewedChoices = reviewedChoices + (row.position to value)
                                choices = choices + (row.position to value)
                                confirm = false
                            }
                        }
                    }
                    HorizontalDivider()
                }
            }
            items(document.issues, key = { "issue-${it.position}" }) {
                Text("Строка ${it.position} исключена · ${it.message}", color = MaterialTheme.colorScheme.error)
            }
        }
    }
    refundCandidate?.let { candidate ->
        val source = refundSource
        val accept: (Operation) -> Unit = { operation ->
            val value = ImportChoice(candidate.row.position, operation, reviewed = true)
            reviewedChoices = reviewedChoices + (candidate.row.position to value)
            choices = choices + (candidate.row.position to value)
            confirm = false
            refundCandidate = null
            refundSource = null
        }
        val local = commands.copy(
            error = null, addOperation = { operation, _ -> accept(operation) },
            reviseOperation = { _, operation, _ -> if (operation != null) accept(operation) },
        )
        if (source == null) {
            RefundSourcePicker(candidate.row, snapshot, local, { refundCandidate = null }) { refundSource = it }
        } else {
            RefundEditor(
                source, snapshot, local, { refundCandidate = null },
                existing = reviewedChoices[candidate.row.position]?.takeUnless { it.linkExisting }?.operation
                    ?.takeIf { it.kind == OperationKind.Refund && it.refundOf == source.id },
                suggestedAmount = candidate.row.signedMinor, suggestedDate = candidate.row.date,
            )
        }
    }
    editing?.let { candidate ->
        val target = requireNotNull(account)
        val existing = reviewedChoices[candidate.row.position]?.takeUnless { it.linkExisting }?.operation
            ?: candidate.row.proposal(target, document.key(target, candidate.row))
        val capture: (Operation) -> Unit = { operation ->
            val value = ImportChoice(candidate.row.position, operation, reviewed = true)
            reviewedChoices = reviewedChoices + (candidate.row.position to value)
            choices = choices + (candidate.row.position to value)
            confirm = false
            editing = null
        }
        val local = commands.copy(
            error = null,
            addOperation = { operation, _ -> capture(operation) },
            reviseOperation = { _, operation, _ -> if (operation != null) capture(operation) },
        )
        OperationEditor(
            snapshot.balances, snapshot.categories, snapshot.month, target, local, { editing = null }, existing,
        )
    }
}
