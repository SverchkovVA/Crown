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
import ru.crown.finance.ledger.Operation
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
    ) : Workspace
    data class Demo(val content: DemoState) : Workspace
    data class Book(
        val name: String,
        val snapshot: LedgerSnapshot,
        val recoveryCode: String? = null,
        val busy: Boolean = false,
        val error: String? = null,
        val message: String? = null,
    ) : Workspace
}

internal class CrownViewModel(application: Application) : AndroidViewModel(application) {
    private val documents = application.contentResolver
    private val repository = BookRepository(application)
    private val demo = DemoRepository(application)
    private var session: BookSession? = null
    private var lockRequested = false
    private var pendingBackup: ByteArray? = null
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
                    mutableState.value = Workspace.Book(book.identity.name, snapshot, recoveryCode)
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
        val current = mutableState.value as? Workspace.Access ?: return
        mutableState.value = current.copy(error = error)
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

    fun prepareBackup(password: CharArray, chooseDestination: () -> Boolean) {
        val current = mutableState.value as? Workspace.Book
        val book = session
        if (current == null || book == null || current.busy || pendingBackup != null) {
            password.fill(' ')
            return
        }
        mutableState.value = current.copy(busy = true, error = null, message = null)
        lockRequested = false
        viewModelScope.launch {
            try {
                val encrypted = book.backup(password)
                mutableState.value = current.copy(error = null, message = null)
                if (lockRequested) {
                    encrypted.fill(0)
                } else {
                    pendingBackup = encrypted
                    if (!chooseDestination()) {
                        pendingBackup = null
                        encrypted.fill(0)
                        mutableState.value = current.copy(error = "Файловый менеджер недоступен")
                    }
                }
            } catch (error: CancellationException) {
                throw error
            } catch (_: Exception) {
                mutableState.value = current.copy(error = "Не удалось подготовить копию книги")
            } finally {
                password.fill(' ')
                if (lockRequested) closeBook()
            }
        }
    }

    fun saveBackup(file: Uri?) {
        val encrypted = pendingBackup ?: return
        pendingBackup = null
        if (file?.scheme != "content") {
            encrypted.fill(0)
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
                        stream.write(encrypted)
                        stream.flush()
                    }
                }
                success = true
            } catch (error: CancellationException) {
                throw error
            } catch (_: Exception) {
                // Книга остаётся открываемой даже при сбое файлового провайдера.
            } finally {
                encrypted.fill(0)
            }
            val message = "Зашифрованная копия сохранена"
            val error = "Не удалось записать копию в выбранный файл"
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

    fun addAccount(name: String, opening: Long, date: LocalDate, saved: () -> Unit) =
        change(saved) { update(YearMonth.from(date)) { addAccount(name, opening, date) } }

    fun addOperation(operation: Operation, saved: () -> Unit) =
        change(saved) { update(YearMonth.from(operation.date)) { addOperation(operation) } }

    fun reviseOperation(original: Operation, replacement: Operation?, saved: () -> Unit) {
        val month = YearMonth.from(replacement?.date ?: original.date)
        change(saved) { update(month) { reviseOperation(original.id, replacement) } }
    }

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
        pendingBackup?.fill(0)
        pendingBackup = null
        try {
            session?.close()
        } finally {
            demo.close()
        }
    }
}

class MainActivity : ComponentActivity() {
    private val model by viewModels<CrownViewModel>()
    private val writeCopy = registerForActivityResult(ActivityResultContracts.StartActivityForResult()) { result ->
        model.saveBackup(result.data?.data.takeIf { result.resultCode == RESULT_OK })
    }
    private val readCopy = registerForActivityResult(ActivityResultContracts.StartActivityForResult()) { result ->
        if (result.resultCode == RESULT_OK) model.setRestoreFile(result.data?.data)
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
                    )
                    is Workspace.Demo -> FinanceScreen(workspace.content, model::showDemo, "Учебная книга", { model.closeBook() })
                    is Workspace.Book -> {
                        val code = workspace.recoveryCode
                        if (code != null) {
                            RecoveryScreen(code, model::acknowledgeRecovery)
                        } else {
                            FinanceScreen(
                                DemoState.Ready(workspace.snapshot), {}, workspace.name, { model.closeBook() },
                                BookCommands(
                                    workspace.busy, workspace.error, model::clearError, model::changeMonth,
                                    model::addAccount, model::addOperation, model::reviseOperation,
                                    backup = { password -> model.prepareBackup(password, ::createCopy) },
                                ),
                                notice = workspace.message,
                            )
                        }
                    }
                }
            }
        }
    }

    private fun createCopy(): Boolean {
        val intent = localDocument(Intent.ACTION_CREATE_DOCUMENT)
            .putExtra(Intent.EXTRA_TITLE, "Crown-Android.crowncopy")
        return try {
            writeCopy.launch(intent)
            true
        } catch (_: ActivityNotFoundException) {
            false
        } catch (_: SecurityException) {
            false
        }
    }

    private fun chooseCopy() {
        val error = "Не удалось открыть файловый менеджер"
        try {
            readCopy.launch(localDocument(Intent.ACTION_OPEN_DOCUMENT))
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
