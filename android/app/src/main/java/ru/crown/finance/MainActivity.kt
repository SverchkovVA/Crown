package ru.crown.finance

import android.app.Application
import android.os.Bundle
import android.view.WindowManager
import androidx.activity.ComponentActivity
import androidx.activity.compose.setContent
import androidx.activity.enableEdgeToEdge
import androidx.activity.viewModels
import androidx.lifecycle.AndroidViewModel
import androidx.lifecycle.compose.collectAsStateWithLifecycle
import androidx.lifecycle.viewModelScope
import kotlinx.coroutines.CancellationException
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
    data class Access(val exists: Boolean, val busy: Boolean = false, val error: String? = null) : Workspace
    data class Demo(val content: DemoState) : Workspace
    data class Book(
        val name: String,
        val snapshot: LedgerSnapshot,
        val recoveryCode: String? = null,
        val busy: Boolean = false,
        val error: String? = null,
    ) : Workspace
}

internal class CrownViewModel(application: Application) : AndroidViewModel(application) {
    private val repository = BookRepository(application)
    private val demo = DemoRepository(application)
    private var session: BookSession? = null
    private var lockRequested = false
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
        mutableState.value = access.copy(busy = true, error = null)
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
                mutableState.value = Workspace.Access(repository.exists, error = failure)
            } catch (_: LinkageError) {
                mutableState.value = Workspace.Access(repository.exists, error = "Недоступно шифрованное хранилище")
            } finally {
                candidate?.close()
                credential.fill(' ')
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
        try {
            session?.close()
        } finally {
            demo.close()
        }
    }
}

class MainActivity : ComponentActivity() {
    private val model by viewModels<CrownViewModel>()

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        window.addFlags(WindowManager.LayoutParams.FLAG_SECURE)
        enableEdgeToEdge()
        setContent {
            val workspace = model.state.collectAsStateWithLifecycle().value
            CrownTheme {
                when (workspace) {
                    is Workspace.Access -> AccessScreen(workspace, model::create, model::open, model::showDemo)
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
                                ),
                            )
                        }
                    }
                }
            }
        }
    }

    override fun onStop() {
        super.onStop()
        if (!isChangingConfigurations) model.closeBook(background = true)
    }
}
