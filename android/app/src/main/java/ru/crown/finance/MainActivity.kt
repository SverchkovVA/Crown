package ru.crown.finance

import android.app.Application
import android.content.ActivityNotFoundException
import android.content.Intent
import android.net.Uri
import android.os.Bundle
import android.os.ParcelFileDescriptor
import android.view.WindowManager
import androidx.activity.ComponentActivity
import androidx.activity.compose.setContent
import androidx.activity.enableEdgeToEdge
import androidx.activity.viewModels
import androidx.activity.result.ActivityResultLauncher
import androidx.activity.result.contract.ActivityResultContracts
import androidx.lifecycle.AndroidViewModel
import androidx.lifecycle.compose.collectAsStateWithLifecycle
import androidx.lifecycle.viewModelScope
import kotlinx.coroutines.CancellationException
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.launch
import ru.crown.finance.ledger.BookRepository
import ru.crown.finance.ledger.BookSession
import ru.crown.finance.ledger.CredentialKind
import ru.crown.finance.ledger.DemoRepository
import ru.crown.finance.ledger.LedgerSnapshot
import ru.crown.finance.ledger.JournalExport
import ru.crown.finance.ledger.JournalFormat
import ru.crown.finance.ledger.Operation
import ru.crown.finance.ledger.OperationKind
import ru.crown.finance.ledger.ReconciliationPreview
import ru.crown.finance.ledger.StatementDocument
import ru.crown.finance.ledger.ImportChoice
import ru.crown.finance.ledger.AccountKind
import ru.crown.finance.ledger.readStatement
import ru.crown.finance.ui.AccessScreen
import ru.crown.finance.ui.BookCommands
import ru.crown.finance.ui.CrownTheme
import ru.crown.finance.ui.FinanceScreen
import ru.crown.finance.ui.RecoveryScreen
import java.time.LocalDate
import java.time.YearMonth

internal sealed interface DemoState {
    data object Loading : DemoState
    data object Failed : DemoState
    data class Ready(val snapshot: LedgerSnapshot) : DemoState
}

internal sealed interface Workspace {
    data class Access(
        val exists: Boolean,
        val busy: Boolean = false,
        val error: String? = null,
        val message: String? = null,
        val restoreFile: Uri? = null,
        val inspectFile: Uri? = null,
    ) : Workspace
    data class Demo(val content: DemoState) : Workspace
    data class Book(
        val name: String,
        val snapshot: LedgerSnapshot,
        val recoveryCode: String? = null,
        val busy: Boolean = false,
        val error: String? = null,
        val message: String? = null,
        val statement: StatementDocument? = null,
    ) : Workspace
}

internal enum class OutputFile(
    val fileName: String,
    val mime: String,
    val success: String,
    val preparationFailure: String,
) {
    Backup("Crown-Android.crowncopy", "application/octet-stream", "Зашифрованная копия сохранена", "Не удалось подготовить копию книги"),
    Csv("Crown-Android-operations.csv", "text/csv", "CSV сохранён", "Не удалось подготовить CSV"),
    Json("Crown-Android-operations.json", "application/json", "JSON сохранён", "Не удалось подготовить JSON"),
}

private data class PendingFile(val kind: OutputFile, val bytes: ByteArray)

internal class CrownViewModel(application: Application) : AndroidViewModel(application) {
    private val documents = application.contentResolver
    private val repository = BookRepository(application)
    private val demo = DemoRepository(application)
    private var session: BookSession? = null
    private var lockRequested = false
    private var pendingFile: PendingFile? = null
    private var pendingStatement: StatementDocument? = null
    private val mutableState = MutableStateFlow<Workspace>(Workspace.Access(repository.exists))
    val state = mutableState.asStateFlow()

    fun showDemo() {
        when (val current = mutableState.value) {
            is Workspace.Access -> if (current.busy) return
            is Workspace.Demo -> if (current.content != DemoState.Failed) return
            is Workspace.Book -> return
        }
        val loading = Workspace.Demo(DemoState.Loading)
        mutableState.value = loading
        viewModelScope.launch {
            val result = try {
                DemoState.Ready(demo.snapshot())
            } catch (error: CancellationException) {
                throw error
            } catch (_: Exception) {
                DemoState.Failed
            }
            if (mutableState.value === loading) mutableState.value = Workspace.Demo(result)
        }
    }

    fun create(name: String, password: CharArray) = access(password, "Не удалось создать книгу") {
        repository.create(name, password)
    }

    fun open(password: CharArray, recovery: Boolean) =
        access(password, "Не удалось открыть книгу. Проверьте данные доступа и повторите попытку.") {
            val kind = if (recovery) CredentialKind.Recovery else CredentialKind.Password
            repository.open(password, kind) to null
        }

    private fun access(
        credential: CharArray,
        failure: String,
        unlock: suspend () -> Pair<BookSession, String?>,
    ) {
        val access = mutableState.value as? Workspace.Access
        if (access == null || access.busy) {
            credential.fill(' ')
            return
        }
        mutableState.value = access.copy(busy = true, error = null, message = null)
        lockRequested = false
        viewModelScope.launch {
            var candidate: BookSession? = null
            try {
                val (book, recoveryCode) = unlock()
                candidate = book
                val snapshot = book.snapshot(YearMonth.now())
                if (lockRequested && recoveryCode == null) {
                    mutableState.value = Workspace.Access(repository.exists)
                } else {
                    session = book
                    candidate = null
                    mutableState.value = Workspace.Book(book.identity.name, snapshot, recoveryCode, statement = pendingStatement)
                    pendingStatement = null
                }
            } catch (error: CancellationException) {
                throw error
            } catch (_: Exception) {
                val bookExists = repository.exists
                mutableState.value = access.copy(
                    exists = bookExists, busy = false, error = failure,
                    restoreFile = access.restoreFile.takeUnless { bookExists },
                )
            } catch (_: LinkageError) {
                mutableState.value = Workspace.Access(repository.exists, error = "Недоступно шифрованное хранилище")
            } finally {
                candidate?.close()
                credential.fill(' ')
            }
        }
    }

    fun fileManagerError(error: String) {
        when (val current = mutableState.value) {
            is Workspace.Access -> mutableState.value = current.copy(error = error)
            is Workspace.Book -> mutableState.value = current.copy(error = error)
            else -> Unit
        }
    }

    fun setRestoreFile(file: Uri?) {
        val state = mutableState.value as? Workspace.Access ?: return
        if (state.busy || state.exists || (file != null && file.scheme != "content")) return
        mutableState.value = state.copy(restoreFile = file, error = null, message = null)
    }

    fun restore(backupPassword: CharArray, newPassword: CharArray) {
        val current = mutableState.value as? Workspace.Access
        val file = current?.restoreFile
        if (file == null || current.exists || current.busy) {
            backupPassword.fill(' ')
            newPassword.fill(' ')
            return
        }
        access(newPassword, "Не удалось восстановить книгу. Проверьте файл и пароль копии.") {
            try {
                repository.restore(newPassword, backupPassword) { checkNotNull(documents.openInputStream(file)) }
            } finally {
                backupPassword.fill(' ')
            }
        }
    }

    fun setStatementFile(file: Uri?) {
        if (file == null || file.scheme != "content") return
        closeBook()
        val current = mutableState.value as? Workspace.Access ?: return
        if (current.busy) return
        pendingStatement = null
        mutableState.value = current.copy(busy = true, message = null, error = null)
        viewModelScope.launch {
            try {
                pendingStatement = withContext(Dispatchers.IO) {
                    checkNotNull(documents.openInputStream(file)).use(::readStatement)
                }
                mutableState.value = current.copy(message = "Выписка загружена. Откройте книгу для проверки строк.", error = null)
            } catch (error: CancellationException) {
                throw error
            } catch (error: IllegalArgumentException) {
                mutableState.value = current.copy(error = error.message ?: "Проверьте формат выписки")
            } catch (_: Exception) {
                mutableState.value = current.copy(error = "Не удалось прочитать выписку")
            }
        }
    }

    fun dismissStatement() {
        val current = mutableState.value as? Workspace.Book ?: return
        if (!current.busy) mutableState.value = current.copy(statement = null, error = null)
    }

    fun importStatement(account: String, choices: List<ImportChoice>) {
        val current = mutableState.value as? Workspace.Book ?: return
        val document = current.statement ?: return
        val month = choices.maxOfOrNull { YearMonth.from(it.operation.date) } ?: current.snapshot.month
        change(saved = {
            val saved = mutableState.value as? Workspace.Book
            if (saved != null) mutableState.value = saved.copy(statement = null, message = "Выбранные строки выписки обработаны")
        }) { update(month) { importStatement(document, account, choices) } }
    }

    fun setInspectFile(file: Uri?) {
        if (file != null && file.scheme != "content") return
        if (file != null && mutableState.value is Workspace.Book) closeBook()
        val current = mutableState.value as? Workspace.Access ?: return
        if (current.busy) return
        mutableState.value = current.copy(inspectFile = file, error = null, message = null)
    }

    fun inspectBackup(password: CharArray) {
        val current = mutableState.value as? Workspace.Access
        val file = current?.inspectFile
        if (file == null || current.busy) {
            password.fill(' ')
            return
        }
        mutableState.value = current.copy(busy = true, error = null, message = null)
        viewModelScope.launch {
            try {
                val copy = repository.inspectBackup(password) { checkNotNull(documents.openInputStream(file)) }
                val totals = "Счета — ${copy.accounts.size}, операции — ${copy.operations.size}, " +
                    "версии изменений — ${copy.revisions.size}, категории — ${copy.categories.size}, " +
                    "теги — ${copy.organization.tags.size}, фильтры — ${copy.organization.filters.size}, " +
                    "правила — ${ru.crown.finance.ledger.currentRules(copy.ruleEvents).size}, " +
                    "бонусные программы — ${copy.bonuses.programs.size}, записи — ${copy.bonuses.entries.size}."
                mutableState.value = current.copy(message = "Копия проверена. $totals", error = null)
            } catch (error: CancellationException) {
                throw error
            } catch (_: Exception) {
                mutableState.value = current.copy(message = null, error = "Не удалось проверить копию. Проверьте файл и пароль.")
            } finally {
                password.fill(' ')
            }
        }
    }

    fun prepareBackup(password: CharArray, chooseDestination: (OutputFile) -> Boolean) =
        prepareFile(OutputFile.Backup, chooseDestination, clear = { password.fill(' ') }) { book ->
            book.backup(password)
        }

    fun prepareExport(preview: JournalExport, format: JournalFormat, chooseDestination: (OutputFile) -> Boolean) {
        val current = mutableState.value as? Workspace.Book ?: return
        if (current.snapshot !== preview.snapshot) return
        val output = when (format) {
            JournalFormat.Csv -> OutputFile.Csv
            JournalFormat.Json -> OutputFile.Json
        }
        prepareFile(output, chooseDestination) { withContext(Dispatchers.IO) { preview.encode(format) } }
    }

    private fun prepareFile(
        output: OutputFile,
        chooseDestination: (OutputFile) -> Boolean,
        clear: () -> Unit = {},
        encode: suspend (BookSession) -> ByteArray,
    ) {
        val current = mutableState.value as? Workspace.Book
        val book = session
        if (current == null || book == null || current.busy || pendingFile != null) {
            clear()
            return
        }
        mutableState.value = current.copy(busy = true, error = null, message = null)
        lockRequested = false
        viewModelScope.launch {
            try {
                val bytes = encode(book)
                mutableState.value = current.copy(error = null, message = null)
                if (lockRequested) {
                    bytes.fill(0)
                } else {
                    pendingFile = PendingFile(output, bytes)
                    if (!chooseDestination(output)) {
                        pendingFile = null
                        bytes.fill(0)
                        mutableState.value = current.copy(error = "Файловый менеджер недоступен")
                    }
                }
            } catch (error: CancellationException) {
                throw error
            } catch (_: Exception) {
                pendingFile?.bytes?.fill(0)
                pendingFile = null
                mutableState.value = current.copy(error = output.preparationFailure)
            } finally {
                clear()
                if (lockRequested) closeBook()
            }
        }
    }

    fun saveFile(file: Uri?) {
        val pending = pendingFile ?: return
        pendingFile = null
        if (file?.scheme != "content") {
            pending.bytes.fill(0)
            return
        }
        val access = mutableState.value as? Workspace.Access
        if (access != null) mutableState.value = access.copy(busy = true, error = null, message = null)
        viewModelScope.launch {
            var success = false
            try {
                withContext(Dispatchers.IO) {
                    val descriptor = checkNotNull(documents.openFileDescriptor(file, "rw"))
                    ParcelFileDescriptor.AutoCloseOutputStream(descriptor).use { stream ->
                        check(descriptor.statSize == 0L)
                        stream.write(pending.bytes)
                        stream.flush()
                        descriptor.fileDescriptor.sync()
                    }
                }
                success = true
            } catch (error: CancellationException) {
                throw error
            } catch (_: Exception) {
                success = false
            } finally {
                pending.bytes.fill(0)
            }
            val message = pending.kind.success
            val error = "Не удалось записать выбранный файл"
            when (val current = mutableState.value) {
                is Workspace.Access -> mutableState.value = current.copy(
                    busy = false, message = message.takeIf { success }, error = error.takeUnless { success },
                )
                is Workspace.Book -> mutableState.value = current.copy(
                    message = message.takeIf { success }, error = error.takeUnless { success },
                )
                else -> Unit
            }
        }
    }

    fun acknowledgeRecovery() {
        val book = mutableState.value as? Workspace.Book ?: return
        mutableState.value = book.copy(recoveryCode = null)
    }

    fun closeBook(background: Boolean = false) {
        when (val current = mutableState.value) {
            is Workspace.Access -> if (current.busy && background) lockRequested = true
            is Workspace.Demo -> if (!background) mutableState.value = Workspace.Access(repository.exists)
            is Workspace.Book -> {
                if (current.recoveryCode != null && background) return
                if (current.busy) {
                    lockRequested = true
                    return
                }
                val previous = session
                session = null
                mutableState.value = Workspace.Access(repository.exists)
                previous?.close()
            }
        }
    }

    fun clearError() {
        val book = mutableState.value as? Workspace.Book ?: return
        if (book.error != null) mutableState.value = book.copy(error = null)
    }

    fun changeMonth(offset: Int) {
        val book = mutableState.value as? Workspace.Book ?: return
        val month = book.snapshot.month.plusMonths(offset.toLong())
        if (month.year in 1900..9999) change { snapshot(month) }
    }

    fun addAccount(
        name: String, opening: Long, date: LocalDate,
        bankId: String?, kind: AccountKind, saved: () -> Unit,
    ) = change(saved) { update(YearMonth.from(date)) { addAccount(name, opening, date, bankId, kind) } }

    fun addOperation(operation: Operation, saved: () -> Unit) =
        change(saved) { update(YearMonth.from(operation.date)) { addOperation(operation) } }

    fun reviseOperation(original: Operation, replacement: Operation?, saved: () -> Unit) {
        val month = YearMonth.from(replacement?.date ?: original.date)
        change(saved) { update(month) { reviseOperation(original, replacement) } }
    }

    fun addCategory(name: String, kind: OperationKind, parentId: String?, saved: () -> Unit) {
        val current = mutableState.value as? Workspace.Book ?: return
        change(saved) { update(current.snapshot.month) { addCategory(name, kind, parentId) } }
    }

    fun addBank(name: String, saved: () -> Unit) {
        val current = mutableState.value as? Workspace.Book ?: return
        change(saved) { update(current.snapshot.month) { addBank(name) } }
    }

    fun changePassword(credential: CharArray, next: CharArray, saved: () -> Unit) {
        val current = mutableState.value as? Workspace.Book
        if (current == null || current.busy || current.recoveryCode != null || session == null) {
            credential.fill(' '); next.fill(' '); return
        }
        change({
            saved()
            val updated = mutableState.value as? Workspace.Book
            if (updated != null) mutableState.value = updated.copy(message = "Парольная фраза изменена")
        }) {
            try {
                val recovery = Regex("CA1-[A-Za-z0-9_-]{43}").matches(credential.concatToString().trim())
                repository.changePassword(identity.id, credential, if (recovery) CredentialKind.Recovery else CredentialKind.Password, next)
                snapshot(current.snapshot.month)
            } finally { credential.fill(' '); next.fill(' ') }
        }
    }

    fun saveCalendarMatch(plan: ru.crown.finance.ledger.PlanningEvent, key: ru.crown.finance.ledger.CalendarPaymentKey, expected: ru.crown.finance.ledger.CalendarMatchEvent?, operation: Operation?, saved: () -> Unit) {
        val current = mutableState.value as? Workspace.Book ?: return
        change(saved) { update(current.snapshot.month) { saveCalendarMatch(plan, key, expected, operation) } }
    }

    fun saveCatalog(expected: ru.crown.finance.ledger.CatalogVersion, value: ru.crown.finance.ledger.CatalogValue, saved: () -> Unit) {
        val current = mutableState.value as? Workspace.Book ?: return
        change(saved) { update(current.snapshot.month) { saveCatalog(expected, value) } }
    }

    fun savePlan(id: String, name: String, value: ru.crown.finance.ledger.PlanningConditions, expected: ru.crown.finance.ledger.PlanningEvent?, archived: Boolean, saved: () -> Unit) {
        val current = mutableState.value as? Workspace.Book ?: return
        change(saved) { update(current.snapshot.month) { savePlan(id, name, value, expected, archived) } }
    }

    fun saveDeposit(id: String, name: String, scenario: ru.crown.finance.ledger.DepositScenario, expected: ru.crown.finance.ledger.DepositEvent?, archived: Boolean, saved: () -> Unit) {
        val current = mutableState.value as? Workspace.Book ?: return
        change(saved) { update(current.snapshot.month) { saveDeposit(id, name, scenario, expected, archived) } }
    }

    fun saveBonusProgram(name: String, expected: ru.crown.finance.ledger.BonusProgramEvent?, archived: Boolean, saved: () -> Unit) {
        val current = mutableState.value as? Workspace.Book ?: return
        change(saved) { update(current.snapshot.month) { saveBonusProgram(name, expected, archived) } }
    }

    fun saveBonusEntry(entry: ru.crown.finance.ledger.BonusEntryEvent, expected: ru.crown.finance.ledger.BonusEntryEvent?, saved: () -> Unit) {
        val current = mutableState.value as? Workspace.Book ?: return
        change(saved) { update(current.snapshot.month) { saveBonusEntry(entry, expected) } }
    }

    fun saveRule(rule: ru.crown.finance.ledger.CategoryRule, expected: ru.crown.finance.ledger.CategoryRuleEvent?, saved: () -> Unit) {
        val current = mutableState.value as? Workspace.Book ?: return
        change(saved) { update(current.snapshot.month) { saveRule(rule, expected) } }
    }

    fun saveTag(name: String, expected: ru.crown.finance.ledger.TagEvent?, archived: Boolean, saved: () -> Unit) {
        val current = mutableState.value as? Workspace.Book ?: return
        change(saved) { update(current.snapshot.month) { saveTag(name, expected, archived) } }
    }

    fun assignTags(id: String, expected: Set<String>, selected: Set<String>, saved: () -> Unit) {
        val current = mutableState.value as? Workspace.Book ?: return
        change(saved) { update(current.snapshot.month) { assignTags(id, expected, selected) } }
    }

    fun saveFilter(name: String, query: ru.crown.finance.ledger.JournalQuery, expected: ru.crown.finance.ledger.FilterEvent?, archived: Boolean, saved: () -> Unit) {
        val current = mutableState.value as? Workspace.Book ?: return
        change(saved) { update(current.snapshot.month) { saveFilter(name, query, expected, archived) } }
    }

    fun setPeriodClosed(month: YearMonth, closed: Boolean, reason: String) =
        change { update(month) { setPeriodClosed(month, closed, reason) } }

    fun reconcile(preview: ReconciliationPreview, actualMinor: Long, saved: () -> Unit) =
        change(saved) { update(YearMonth.from(preview.date)) { reconcile(preview, actualMinor) } }

    private fun change(saved: () -> Unit = {}, action: suspend BookSession.() -> LedgerSnapshot) {
        val current = mutableState.value as? Workspace.Book ?: return
        val book = session ?: return
        if (current.busy || current.recoveryCode != null) return
        mutableState.value = current.copy(busy = true, error = null)
        lockRequested = false
        viewModelScope.launch {
            try {
                val snapshot = book.action()
                mutableState.value = current.copy(snapshot = snapshot, error = null)
                saved()
            } catch (error: CancellationException) {
                throw error
            } catch (error: IllegalArgumentException) {
                mutableState.value = current.copy(error = error.message ?: "Проверьте заполненные поля")
            } catch (_: ArithmeticException) {
                mutableState.value = current.copy(error = "Сумма превышает допустимый диапазон")
            } catch (_: Exception) {
                mutableState.value = current.copy(error = "Не удалось выполнить действие. Повторите попытку.")
            } finally {
                if (lockRequested) closeBook()
            }
        }
    }

    override fun onCleared() {
        pendingFile?.bytes?.fill(0)
        pendingFile = null
        pendingStatement = null
        try {
            session?.close()
        } finally {
            demo.close()
        }
    }
}

class MainActivity : ComponentActivity() {
    private val model by viewModels<CrownViewModel>()
    private val writeDocument = registerForActivityResult(ActivityResultContracts.StartActivityForResult()) { result ->
        model.saveFile(result.data?.data.takeIf { result.resultCode == RESULT_OK })
    }
    private val readCopy = registerForActivityResult(ActivityResultContracts.StartActivityForResult()) { result ->
        if (result.resultCode == RESULT_OK) model.setRestoreFile(result.data?.data)
    }

    private val readStatement = registerForActivityResult(ActivityResultContracts.StartActivityForResult()) { result ->
        if (result.resultCode == RESULT_OK) model.setStatementFile(result.data?.data)
    }
    private val verifyCopy = registerForActivityResult(ActivityResultContracts.StartActivityForResult()) { result ->
        if (result.resultCode == RESULT_OK) model.setInspectFile(result.data?.data)
    }

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        window.addFlags(WindowManager.LayoutParams.FLAG_SECURE)
        enableEdgeToEdge()
        setContent {
            val workspace = model.state.collectAsStateWithLifecycle().value
            CrownTheme {
                when (workspace) {
                    is Workspace.Access -> AccessScreen(
                        workspace, model::create, model::open, model::showDemo,
                        chooseBackup = ::chooseCopy,
                        cancelRestore = { model.setRestoreFile(null) },
                        restore = model::restore,
                        cancelInspect = { model.setInspectFile(null) },
                        inspect = model::inspectBackup,
                    )
                    is Workspace.Demo -> FinanceScreen(workspace.content, model::showDemo, "Учебная книга", { model.closeBook() })
                    is Workspace.Book -> {
                        val code = workspace.recoveryCode
                        if (code != null) {
                            RecoveryScreen(code, model::acknowledgeRecovery)
                        } else {
                            val commands = BookCommands(
                                    workspace.busy, workspace.error, model::clearError, model::changeMonth,
                                    model::addAccount, model::addOperation, model::reviseOperation,
                                    backup = { password -> model.prepareBackup(password, ::createFile) },
                                    export = { preview, format -> model.prepareExport(preview, format, ::createFile) },
                                    addCategory = model::addCategory,
                                    inspectBackup = { openCopy(verifyCopy) },
                                    setPeriodClosed = model::setPeriodClosed,
                                    reconcile = model::reconcile,
                                    chooseStatement = { openStatement() },
                                    importStatement = model::importStatement,
                                    addBank = model::addBank,
                                    saveTag = model::saveTag, assignTags = model::assignTags, saveFilter = model::saveFilter, saveRule = model::saveRule,
                                    saveBonusProgram = model::saveBonusProgram, saveBonusEntry = model::saveBonusEntry, changePassword = model::changePassword, saveDeposit = model::saveDeposit, savePlan = model::savePlan, saveCatalog = model::saveCatalog, saveCalendarMatch = model::saveCalendarMatch,
                                )
                            if (workspace.statement != null) {
                                ru.crown.finance.ui.StatementReview(
                                    workspace.statement, workspace.snapshot, commands, model::dismissStatement,
                                )
                            } else {
                                FinanceScreen(
                                    DemoState.Ready(workspace.snapshot), {}, workspace.name, { model.closeBook() },
                                    commands, notice = workspace.message,
                                )
                            }
                        }
                    }
                }
            }
        }
    }

    private fun createFile(output: OutputFile): Boolean {
        val intent = localDocument(Intent.ACTION_CREATE_DOCUMENT)
            .setType(output.mime)
            .putExtra(Intent.EXTRA_TITLE, output.fileName)
        return try {
            writeDocument.launch(intent)
            true
        } catch (_: ActivityNotFoundException) {
            false
        } catch (_: SecurityException) {
            false
        }
    }

    private fun openStatement() {
        try {
            readStatement.launch(localDocument(Intent.ACTION_OPEN_DOCUMENT).setType("*/*"))
        } catch (_: ActivityNotFoundException) {
            model.fileManagerError("Не удалось открыть файловый менеджер")
        } catch (_: SecurityException) {
            model.fileManagerError("Не удалось открыть файловый менеджер")
        }
    }

    private fun chooseCopy() = openCopy(readCopy)

    private fun openCopy(launcher: ActivityResultLauncher<Intent>) {
        val error = "Не удалось открыть файловый менеджер"
        try {
            launcher.launch(localDocument(Intent.ACTION_OPEN_DOCUMENT))
        } catch (_: ActivityNotFoundException) {
            model.fileManagerError(error)
        } catch (_: SecurityException) {
            model.fileManagerError(error)
        }
    }

    private fun localDocument(action: String) = Intent(action)
        .addCategory(Intent.CATEGORY_OPENABLE)
        .setType("application/octet-stream")
        .putExtra(Intent.EXTRA_LOCAL_ONLY, true)

    override fun onStop() {
        super.onStop()
        if (!isChangingConfigurations) model.closeBook(background = true)
    }
}
