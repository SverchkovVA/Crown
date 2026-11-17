package ru.crown.finance.ui

import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.selection.toggleable
import androidx.compose.foundation.verticalScroll
import androidx.compose.material3.Button
import androidx.compose.material3.Checkbox
import androidx.compose.material3.ExperimentalMaterial3Api
import androidx.compose.material3.LinearProgressIndicator
import androidx.compose.material3.ModalBottomSheet
import androidx.compose.material3.Text
import androidx.compose.material3.rememberModalBottomSheetState
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.semantics.Role
import androidx.compose.ui.unit.dp
import ru.crown.finance.ledger.JournalExport
import ru.crown.finance.ledger.JournalFormat
import ru.crown.finance.ledger.LedgerSnapshot

@OptIn(ExperimentalMaterial3Api::class)
@Composable
internal fun JournalExportForm(snapshot: LedgerSnapshot, month: String, commands: BookCommands, close: () -> Unit) {
    var format by remember { mutableStateOf(JournalFormat.Csv) }
    var names by remember { mutableStateOf(true) }
    var descriptions by remember { mutableStateOf(false) }
    val preview = remember(snapshot, names, descriptions) { JournalExport(snapshot, names, descriptions) }
    ModalBottomSheet(
        onDismissRequest = { if (!commands.busy) close() },
        sheetState = rememberModalBottomSheetState(skipPartiallyExpanded = true),
    ) {
        Column(
            Modifier.fillMaxWidth().padding(start = 24.dp, end = 24.dp, bottom = 16.dp),
            verticalArrangement = Arrangement.spacedBy(10.dp),
        ) {
            WorkspaceTitle("Экспорт журнала")
            Column(Modifier.weight(1f, fill = false).verticalScroll(rememberScrollState())) {
                Text(month)
                Text("Операций в файле — ${preview.count}")
                Choice("Формат", format, JournalFormat.entries, { it.name.uppercase() }, !commands.busy) { format = it }
                ExportOption("Названия счетов и категорий", names, !commands.busy) { names = it }
                ExportOption("Описания операций", descriptions, !commands.busy) { descriptions = it }
                Text("Файл будет содержать открытые данные.")
            }
            commands.error?.let { FormError(it) }
            if (commands.busy) LinearProgressIndicator(Modifier.fillMaxWidth())
            Button(
                onClick = { commands.export(preview, format) },
                enabled = preview.count > 0 && !commands.busy,
                modifier = Modifier.fillMaxWidth(),
            ) { Text("Сохранить экспорт") }
        }
    }
}

@Composable
private fun ExportOption(label: String, selected: Boolean, enabled: Boolean, change: (Boolean) -> Unit) {
    Row(
        Modifier.fillMaxWidth().toggleable(selected, enabled = enabled, role = Role.Checkbox, onValueChange = change),
        verticalAlignment = Alignment.CenterVertically,
    ) {
        Checkbox(selected, onCheckedChange = null, enabled = enabled, modifier = Modifier.padding(12.dp))
        Text(label)
    }
}
