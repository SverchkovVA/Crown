"""Русские настройки состава и просмотр экспортируемого снимка до выбора файла."""

import csv
import io
import json
from collections import Counter
from dataclasses import dataclass, replace
from html import escape

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QAbstractItemView,
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QHeaderView,
    QLabel,
    QPushButton,
    QTableView,
    QVBoxLayout,
    QWidget,
)

from personal_finance.domain.exporting import (
    CSV_COLUMNS,
    ExportFormat,
    ExportPreview,
    ExportSettings,
    serialize_journal_export,
)
from personal_finance.domain.ledger import OPERATION_LABELS
from personal_finance.domain.models import Account, Category, Institution
from personal_finance.domain.organization import Tag
from personal_finance.domain.query import OperationQuery
from personal_finance.presentation.organization import describe_query
from personal_finance.presentation.query_dialog import QueryDialog
from personal_finance.presentation.tables import replace_table

PREVIEW_ROWS = 200


def _label(text: str) -> QLabel:
    result = QLabel(text)
    result.setTextFormat(Qt.TextFormat.PlainText)
    result.setWordWrap(True)
    return result


@dataclass(frozen=True, slots=True)
class ExportOptions:
    settings: ExportSettings
    format: ExportFormat


class ExportOptionsDialog(QDialog):
    def __init__(
        self,
        query: OperationQuery,
        accounts: list[Account],
        categories: list[Category],
        institutions: list[Institution],
        tags: list[Tag],
        *,
        allow_tags: bool,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("Настроить экспорт журнала")
        self.resize(700, 520)
        self.options: ExportOptions | None = None
        self.query = query
        self._accounts, self._categories, self._institutions, self._tags = (
            accounts,
            categories,
            institutions,
            tags,
        )
        layout = QVBoxLayout(self)
        layout.addWidget(
            _label(
                "Экспорт сохраняет выбранные операции в открытый файл CSV или JSON. "
                "Состав можно проверить перед сохранением. Резервная копия создаётся отдельно."
            )
        )
        form = QFormLayout()
        self.format = QComboBox()
        self.format.addItem("CSV — для таблиц", ExportFormat.CSV.value)
        self.format.addItem("JSON — для обработки данных", ExportFormat.JSON.value)
        form.addRow("Формат", self.format)
        self.names = QCheckBox("Названия счетов, банков и категорий")
        self.names.setChecked(True)
        self.notes = QCheckBox("Примечания операций")
        self.tags = QCheckBox("Теги операций")
        self.tags.setEnabled(allow_tags)
        for widget in (self.names, self.notes, self.tags):
            form.addRow(widget)
        layout.addLayout(form)
        self.query_summary = _label("")
        layout.addWidget(self.query_summary)
        self.query_button = QPushButton("Условия выборки…")
        self.query_button.clicked.connect(self.edit_query)
        layout.addWidget(self.query_button)
        layout.addWidget(
            _label(
                "Можно выбрать период, счета, банки, категории и виды операций. "
                "Перевод, части и комиссия остаются целой операцией."
            )
        )
        layout.addStretch()
        self.buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        self.buttons.button(QDialogButtonBox.StandardButton.Ok).setText("Показать предпросмотр")
        self.buttons.button(QDialogButtonBox.StandardButton.Cancel).setText("Отмена")
        self.buttons.accepted.connect(self._validate)
        self.buttons.rejected.connect(self.reject)
        layout.addWidget(self.buttons)
        self._describe_query()

    def _describe_query(self) -> None:
        summary = describe_query(
            self.query, self._accounts, self._categories, self._institutions, self._tags
        )
        self.query_summary.setText(summary if len(summary) <= 260 else summary[:260] + "…")
        self.query_summary.setToolTip("<p>" + escape(summary).replace("\n", "<br>") + "</p>")

    def edit_query(self) -> None:
        dialog = QueryDialog(
            self.query, self._accounts, self._categories, self._institutions, self._tags, self
        )
        dialog.setWindowTitle("Условия экспорта")
        if dialog.exec() == QDialog.DialogCode.Accepted and dialog.result_query is not None:
            self.query = dialog.result_query
            self._describe_query()

    def _validate(self) -> None:
        self.options = ExportOptions(
            ExportSettings(
                self.query, self.notes.isChecked(), self.tags.isChecked(), self.names.isChecked()
            ),
            ExportFormat(self.format.currentData()),
        )
        self.accept()


class ExportPreviewDialog(QDialog):
    def __init__(
        self, preview: ExportPreview, format: ExportFormat, parent: QWidget | None = None
    ) -> None:
        super().__init__(parent)
        self.preview, self.format = preview, format
        self.setWindowTitle("Проверить экспорт перед сохранением")
        self.resize(1040, 650)
        layout = QVBoxLayout(self)
        count = len(preview.operations)
        kinds = Counter(operation.draft.kind for operation in preview.operations)
        self.summary = _label(
            f"Операций в файле: {count}. "
            + "; ".join(
                f"{OPERATION_LABELS[kind]}: {value}" for kind, value in sorted(kinds.items())
            )
        )
        layout.addWidget(self.summary)
        fields = ["даты, виды, суммы, комиссии, счета, категории, части и связи"]
        if preview.settings.include_names:
            fields.append("названия")
        if preview.settings.include_notes:
            fields.append("примечания")
        if preview.settings.include_tags:
            fields.append("теги")
        self.fields = _label("Состав файла: " + "; ".join(fields) + ".")
        layout.addWidget(self.fields)
        layout.addWidget(
            _label(
                "Файл не зашифрован. Сохраняется этот снимок; дальнейшие изменения книги "
                "не меняют уже подготовленную выборку."
            )
        )
        self.table = QTableView()
        self.table.verticalHeader().hide()
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setWordWrap(False)
        limited = replace(preview, operations=preview.operations[:PREVIEW_ROWS])
        if format == ExportFormat.CSV:
            contents = serialize_journal_export(limited, format).decode("utf-8-sig")
            parsed = list(csv.reader(io.StringIO(contents), delimiter=";"))
            headers, rows = parsed[0], parsed[1:]
        else:
            exported = limited.rows()
            if exported:
                keys = list(exported[0])
                priority = [
                    "date",
                    "kind",
                    "account_name",
                    "bank_name",
                    "destination_account_name",
                    "amount_minor",
                    "currency",
                    "fee_minor",
                    "category_name",
                    "note",
                    "tag_names",
                ]
                keys = [key for key in priority if key in keys] + [
                    key for key in keys if key not in priority
                ]
                aliases = {
                    "parts": "parts_json",
                    "tag_ids": "tag_ids_json",
                    "tag_names": "tag_names_json",
                }
                headers = [CSV_COLUMNS[aliases.get(key, key)] for key in keys]
                rows = [[self._json_cell(row[key]) for key in keys] for row in exported]
            else:
                headers, rows = ["Дата", "Вид операции", "Сумма, мин. ед.", "Валюта"], []
        self.model = replace_table(self.table, headers, rows)
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
        self.table.horizontalHeader().setMaximumSectionSize(400)
        layout.addWidget(self.table, 1)
        self.limit = _label(
            f"Показано строк: {min(count, PREVIEW_ROWS)} из {count}. "
            "В файл сохранятся все выбранные операции."
        )
        layout.addWidget(self.limit)
        self.buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel
        )
        self.buttons.button(QDialogButtonBox.StandardButton.Save).setText("Сохранить файл…")
        self.buttons.button(QDialogButtonBox.StandardButton.Cancel).setText("Отмена")
        self.buttons.accepted.connect(self.accept)
        self.buttons.rejected.connect(self.reject)
        layout.addWidget(self.buttons)

    @staticmethod
    def _json_cell(value: object) -> str:
        if value is None:
            return ""
        if isinstance(value, str):
            return value
        return json.dumps(value, ensure_ascii=False, separators=(",", ":"))
