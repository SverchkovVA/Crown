package ru.crown.finance

import android.app.Application
import android.os.Bundle
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
import ru.crown.finance.ledger.DemoRepository
import ru.crown.finance.ledger.LedgerSnapshot
import ru.crown.finance.ui.CrownTheme
import ru.crown.finance.ui.FinanceScreen

internal sealed interface DemoState {
    data object Loading : DemoState
    data object Failed : DemoState
    data class Ready(val snapshot: LedgerSnapshot) : DemoState
}

internal class DemoViewModel(application: Application) : AndroidViewModel(application) {
    private val repository = DemoRepository(application)
    private val mutableState = MutableStateFlow<DemoState>(DemoState.Loading)
    val state = mutableState.asStateFlow()

    init {
        load()
    }

    fun retry() {
        if (mutableState.value == DemoState.Failed) load()
    }

    private fun load() {
        mutableState.value = DemoState.Loading
        viewModelScope.launch {
            mutableState.value = try {
                DemoState.Ready(repository.snapshot())
            } catch (error: CancellationException) {
                throw error
            } catch (_: Exception) {
                DemoState.Failed
            }
        }
    }

    override fun onCleared() = repository.close()
}

class MainActivity : ComponentActivity() {
    private val model by viewModels<DemoViewModel>()

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        enableEdgeToEdge()
        setContent {
            val state = model.state.collectAsStateWithLifecycle().value
            CrownTheme { FinanceScreen(state, model::retry) }
        }
    }
}
