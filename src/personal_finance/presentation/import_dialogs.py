"""Настройка локального файла и исправляемый предпросмотр перед единой записью."""

from dataclasses import dataclass
from html import escape

from PySide6.QtCore import QDate, Qt
from PySide6.QtWidgets import (
    QAbstractItemView,
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QMessageBox,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from personal_finance.application.classification import PreparedImportCategories
from personal_finance.application.importing import Importing
from personal_finance.domain.classification import ClassificationStatus
from personal_finance.domain.errors import FinanceError, ValidationError
from personal_finance.domain.imports import (
    ImportIssue,
    ImportPreview,
    ImportPreviewLine,
    ImportSelection,
    ImportStatus,
    account_movement,
)
from personal_finance.domain.ledger import (
    OPERATION_LABELS,
    Operation,
    OperationDraft,
    OperationKind,
)
from personal_finance.domain.models import Account, Category, CategoryKind, Institution, Money
from personal_finance.domain.organization import Tag
from personal_finance.domain.refunds import RefundCandidate
from personal_finance.infrastructure.importing.statements import CsvMapping
from personal_finance.presentation.ledger_dialogs import OperationDialog, category_title
from personal_finance.presentation.money import format_minor, format_money


def plain(text: str) -> QLabel:
    label = QLabel(text)
    label.setTextFormat(Qt.TextFormat.PlainText)
    label.setWordWrap(True)
    return label


@dataclass(frozen=True, slots=True)
class ImportOptions:
    account_id: str
    expense_category_id: str | None
    income_category_id: str | None
    mapping: CsvMapping | None
    use_rules: bool = True


class ImportOptionsDialog(QDialog):
    def __init__(
        self,
        contents: bytes,
        *,
        is_csv: bool,
        accounts: list[Account],
        categories: list[Category],
        allow_rules: bool = False,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("Настроить импорт CSV" if is_csv else "Настроить импорт OFX")
        self.resize(720, 610)
        self.options: ImportOptions | None = None
        self._contents, self._is_csv = contents, is_csv
        layout = QVBoxLayout(self)
        layout.addWidget(
            plain(
                "Выберите счёт, которому принадлежит выписка. Файл читается только на этом ПК. "
                "Следующее окно покажет строки; на этом шаге книга не меняется."
            )
        )
        tabs = QTabWidget()
        general = QWidget()
        form = QFormLayout(general)
        self.account = QComboBox()
        self.account.addItem("Выберите счёт RUB", None)
        for account in accounts:
            if account.currency == "RUB":
                self.account.addItem(account.name, account.id)
        form.addRow("Счёт выписки", self.account)
        self.expense_category, self.income_category = QComboBox(), QComboBox()
        for combo, kind in (
            (self.expense_category, CategoryKind.EXPENSE),
            (self.income_category, CategoryKind.INCOME),
        ):
            combo.addItem("Выбирать отдельно для каждой строки", None)
            for category in categories:
                if category.kind == kind:
                    combo.addItem(category_title(category, categories), category.id)
        form.addRow("Категория расходов", self.expense_category)
        form.addRow("Категория доходов", self.income_category)
        self.use_rules = QCheckBox("Предлагать категории по правилам")
        self.use_rules.setEnabled(allow_rules)
        self.use_rules.setChecked(allow_rules)
        form.addRow(self.use_rules)
        form.addRow(
            plain(
                "Общие категории только заполняют предпросмотр. Переводы, возвраты, части и теги "
                "можно уточнить в каждой строке. Начальные остатки импорт не создаёт. "
                "Выбранная общая категория имеет приоритет над правилами."
            )
        )
        tabs.addTab(general, "Книга и категории")
        self.delimiter, self.encoding = QComboBox(), QComboBox()
        for title, value in (("Точка с запятой ;", ";"), ("Запятая ,", ","), ("Табуляция", "\t")):
            self.delimiter.addItem(title, value)
        self.encoding.addItem("UTF-8 (включая BOM)", "utf-8-sig")
        self.encoding.addItem("Windows-1251", "cp1251")
        self.date_format, self.decimal = QComboBox(), QComboBox()
        self.date_format.addItem("ДД.ММ.ГГГГ", "%d.%m.%Y")
        self.date_format.addItem("ГГГГ-ММ-ДД", "%Y-%m-%d")
        self.decimal.addItem("Запятая: -123,45", ",")
        self.decimal.addItem("Точка: -123.45", ".")
        self.columns = [QComboBox() for _ in range(5)]
        self.column_status = plain("")
        if is_csv:
            csv_page = QWidget()
            csv_form = QFormLayout(csv_page)
            for title, widget in (
                ("Разделитель полей", self.delimiter),
                ("Кодировка", self.encoding),
                ("Формат даты", self.date_format),
                ("Десятичный знак", self.decimal),
            ):
                csv_form.addRow(title, widget)
            for title, combo in zip(
                ("Столбец даты", "Сумма со знаком", "Описание", "Внешний ID", "Код валюты"),
                self.columns,
                strict=True,
            ):
                csv_form.addRow(title, combo)
            csv_form.addRow(self.column_status)
            tabs.addTab(csv_page, "Столбцы CSV")
            self.delimiter.currentIndexChanged.connect(self._refresh_columns)
            self.encoding.currentIndexChanged.connect(self._refresh_columns)
            self._refresh_columns()
        layout.addWidget(tabs, 1)
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.button(QDialogButtonBox.StandardButton.Ok).setText("Показать предпросмотр")
        buttons.button(QDialogButtonBox.StandardButton.Cancel).setText("Отмена")
        buttons.accepted.connect(self._validate)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def _refresh_columns(self) -> None:
        try:
            names = Importing.columns(
                self._contents, self.delimiter.currentData(), self.encoding.currentData()
            )
        except FinanceError as error:
            names = ()
            self.column_status.setText(str(error))
        else:
            self.column_status.setText(
                "Нужен заголовок и один столбец суммы со знаком: расход отрицательный."
            )
        suggestions = (
            ("дата", "date"),
            ("сумма", "amount"),
            ("описание", "примечание", "description", "memo"),
            ("id", "fitid"),
            ("валюта", "currency"),
        )
        for index, combo in enumerate(self.columns):
            previous = combo.currentData()
            combo.clear()
            combo.addItem("Выберите столбец" if index < 2 else "Не использовать", None)
            for name in names:
                combo.addItem(name, name)
            selected = (
                previous
                if previous in names
                else next((n for n in names if n.casefold().strip() in suggestions[index]), None)
            )
            combo.setCurrentIndex(max(0, combo.findData(selected)))

    def _validate(self) -> None:
        try:
            account = self.account.currentData()
            if account is None:
                raise ValidationError("Выберите счёт выписки.")
            mapping = (
                CsvMapping(
                    self.columns[0].currentData(),
                    self.columns[1].currentData(),
                    self.columns[2].currentData(),
                    self.columns[3].currentData(),
                    self.columns[4].currentData(),
                    delimiter=self.delimiter.currentData(),
                    encoding=self.encoding.currentData(),
                    date_format=self.date_format.currentData(),
                    decimal_separator=self.decimal.currentData(),
                )
                if self._is_csv
                else None
            )
            self.options = ImportOptions(
                account,
                self.expense_category.currentData(),
                self.income_category.currentData(),
                mapping,
                self.use_rules.isChecked(),
            )
            self.accept()
        except FinanceError as error:
            QMessageBox.warning(self, "Проверьте настройки", str(error))


class ImportPreviewDialog(QDialog):
    def __init__(
        self,
        preview: ImportPreview,
        options: ImportOptions,
        accounts: list[Account],
        categories: list[Category],
        institutions: list[Institution],
        tags: list[Tag],
        refund_candidates: list[RefundCandidate],
        parent: QWidget | None = None,
        *,
        classification: PreparedImportCategories | None = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("Проверить строки перед импортом")
        self.resize(1060, 650)
        self.preview, self.options = preview, options
        self._accounts, self._categories, self._institutions = accounts, categories, institutions
        self._tags, self._refund_candidates = tags, refund_candidates
        self._lines = {line.row.position: line for line in preview.lines}
        self._drafts: dict[int, OperationDraft] = {}
        self._links: dict[int, Operation] = {}
        self._classification_hints: dict[int, str] = {}
        self.selections: tuple[ImportSelection, ...] = ()
        self._category_titles = {
            category.id: category_title(category, categories) for category in categories
        }
        decisions = dict(classification.decisions) if classification is not None else {}
        rule_names = dict(classification.rule_names) if classification is not None else {}
        for line in preview.lines:
            kind = OperationKind.INCOME if line.row.amount.minor > 0 else OperationKind.EXPENSE
            category = (
                options.income_category_id
                if kind == OperationKind.INCOME
                else options.expense_category_id
            )
            suggestion = decisions.get(line.row.position) if options.use_rules else None
            if suggestion is not None and category is None:
                explanation = "; ".join(
                    rule_names.get(identifier, "Недоступное правило")
                    for identifier in suggestion.rule_ids[:3]
                )
                if len(suggestion.rule_ids) > 3:
                    explanation += f"; ещё {len(suggestion.rule_ids) - 3}"
                if suggestion.status == ClassificationStatus.SUGGESTED:
                    category = suggestion.category_id
                    self._classification_hints[line.row.position] = "Правило: " + explanation
                elif suggestion.status == ClassificationStatus.CONFLICT:
                    self._classification_hints[line.row.position] = (
                        "Конфликт правил: " + explanation + ". Выберите категорию вручную."
                    )
            if category is not None:
                self._drafts[line.row.position] = OperationDraft(
                    kind,
                    line.row.occurred_on,
                    options.account_id,
                    Money(abs(line.row.amount.minor)),
                    category,
                    note=line.row.note,
                )
        layout = QVBoxLayout(self)
        account_name = next((a.name for a in accounts if a.id == options.account_id), "—")
        layout.addWidget(plain("Счёт выписки: " + account_name))
        layout.addWidget(
            plain(
                "Отметьте строки для записи. Для возможного повтора флажок означает создание "
                "отдельной операции; связь с существующей не создаёт нового движения. "
                "Ошибки и известные повторы исключены. Книга изменится только после подтверждения."
            )
        )
        for warning in preview.document.warnings:
            layout.addWidget(plain(warning))
        records: list[ImportPreviewLine | ImportIssue] = sorted(
            list(preview.lines) + list(preview.document.issues),
            key=lambda value: (
                value.row.position if isinstance(value, ImportPreviewLine) else value.position
            ),
        )
        self.table = QTableWidget(len(records), 7)
        self.table.setStyleSheet(
            "QTableWidget::indicator:unchecked { width: 16px; height: 16px; "
            "border: 1px solid #668577; background: white; }"
        )
        self.table.setHorizontalHeaderLabels(
            [
                "Включить",
                "Строка",
                "Дата",
                "Движение, RUB",
                "Вид / категория / связь",
                "Примечание",
                "Проверка",
            ]
        )
        self.table.verticalHeader().hide()
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
        for column, width in ((4, 170), (5, 220), (6, 300)):
            self.table.horizontalHeader().setSectionResizeMode(
                column, QHeaderView.ResizeMode.Interactive
            )
            self.table.setColumnWidth(column, width)
        self._table_rows: dict[int, int] = {}
        for index, record in enumerate(records):
            if isinstance(record, ImportPreviewLine):
                self._table_rows[record.row.position] = index
                check = QTableWidgetItem()
                enabled = record.status in (ImportStatus.NEW, ImportStatus.POSSIBLE)
                check.setFlags(
                    Qt.ItemFlag.ItemIsEnabled
                    | Qt.ItemFlag.ItemIsSelectable
                    | Qt.ItemFlag.ItemIsUserCheckable
                    if enabled
                    else Qt.ItemFlag.ItemIsSelectable
                )
                check.setCheckState(
                    Qt.CheckState.Checked
                    if record.status == ImportStatus.NEW and record.row.position in self._drafts
                    else Qt.CheckState.Unchecked
                )
                self.table.setItem(index, 0, check)
                self._render(record.row.position)
            else:
                for column, value in ((1, str(record.position)), (6, record.message)):
                    self.table.setItem(index, column, QTableWidgetItem(value))
        layout.addWidget(self.table, 1)
        actions = QHBoxLayout()
        self.edit_button = QPushButton("Исправить строку…")
        self.link_button = QPushButton("Связать с существующей…")
        self.edit_button.clicked.connect(self.edit_selected)
        self.link_button.clicked.connect(self.link_selected)
        actions.addWidget(self.edit_button)
        actions.addWidget(self.link_button)
        actions.addStretch()
        layout.addLayout(actions)
        self.summary = plain("")
        layout.addWidget(self.summary)
        self.buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel
        )
        self.buttons.button(QDialogButtonBox.StandardButton.Save).setText("Подтвердить импорт")
        self.buttons.button(QDialogButtonBox.StandardButton.Cancel).setText("Отмена")
        self.buttons.accepted.connect(self._validate)
        self.buttons.rejected.connect(self.reject)
        layout.addWidget(self.buttons)
        self.table.itemChanged.connect(self._update_actions)
        self.table.itemSelectionChanged.connect(self._update_actions)
        self._update_actions()

    def _render(self, position: int) -> None:
        line, index = self._lines[position], self._table_rows[position]
        draft = self._drafts.get(position)
        kind_description = OPERATION_LABELS[draft.kind] if draft else "Укажите категорию"
        if draft is not None and draft.category_id is not None:
            kind_description += " · " + self._category_titles.get(
                draft.category_id, "Недоступная категория"
            )
        elif draft is not None and draft.parts:
            kind_description += f" · частей: {len(draft.parts)}"
        values = (
            str(position),
            (draft.occurred_on if draft else line.row.occurred_on).strftime("%d.%m.%Y"),
            format_minor(
                account_movement(draft, self.options.account_id)
                if draft
                else line.row.amount.minor,
                line.row.amount.currency,
                include_currency=False,
            ),
            ("Связь: " if position in self._links else "") + kind_description,
            draft.note if draft else line.row.note,
            " ".join(
                value
                for value in (line.message, self._classification_hints.get(position, ""))
                if value
            ),
        )
        self.table.blockSignals(True)
        for column, value in enumerate(values, 1):
            item = QTableWidgetItem(value)
            item.setToolTip("<p>" + escape(value).replace("\n", "<br>") + "</p>")
            self.table.setItem(index, column, item)
        self.table.blockSignals(False)

    def _selected(self) -> ImportPreviewLine | None:
        index = self.table.currentRow()
        return next(
            (line for pos, line in self._lines.items() if self._table_rows[pos] == index), None
        )

    def _chosen(self) -> tuple[ImportSelection, ...]:
        values = []
        for position, index in self._table_rows.items():
            item = self.table.item(index, 0)
            assert item is not None
            if item.checkState() != Qt.CheckState.Checked:
                continue
            line = self._lines[position]
            if line.status not in (ImportStatus.NEW, ImportStatus.POSSIBLE):
                raise ValidationError("Повторную или противоречивую строку импортировать нельзя.")
            draft = self._drafts.get(position)
            if draft is None:
                raise ValidationError(
                    "Для каждой выбранной строки задайте категорию или вид операции."
                )
            delta = account_movement(draft, self.options.account_id)
            if (
                delta == 0
                or (delta > 0) != (line.row.amount.minor > 0)
                or draft.amount.currency != line.row.amount.currency
            ):
                raise ValidationError("Сохраните валюту и направление движения выбранного счёта.")
            linked = self._links.get(position)
            values.append(
                ImportSelection(
                    position,
                    draft,
                    line.status == ImportStatus.POSSIBLE,
                    linked.id if linked else None,
                    linked.revision_id if linked else None,
                )
            )
        return tuple(values)

    def _update_actions(self) -> None:
        selected = self._selected()
        editable = selected is not None and selected.status in (
            ImportStatus.NEW,
            ImportStatus.POSSIBLE,
        )
        self.edit_button.setEnabled(editable)
        self.link_button.setEnabled(bool(editable and selected is not None and selected.candidates))
        try:
            chosen = self._chosen()
        except FinanceError as error:
            self.summary.setText(str(error))
            self.buttons.button(QDialogButtonBox.StandardButton.Save).setEnabled(False)
            return
        linked = sum(value.existing_operation_id is not None for value in chosen)
        delta = sum(
            account_movement(value.draft, self.options.account_id)
            for value in chosen
            if value.existing_operation_id is None
        )
        self.summary.setText(
            f"Будет создано: {len(chosen) - linked}. Связей: {linked}. "
            + "Изменение выбранного счёта: "
            + format_minor(delta, Money(0).currency)
            + "."
        )
        self.buttons.button(QDialogButtonBox.StandardButton.Save).setEnabled(bool(chosen))

    def edit_selected(self) -> None:
        line = self._selected()
        if line is None or line.status not in (ImportStatus.NEW, ImportStatus.POSSIBLE):
            return
        dialog = OperationDialog(
            self._accounts,
            self._categories,
            self._institutions,
            self,
            initial_draft=self._drafts.get(line.row.position),
            preview_mode=True,
            kind=OperationKind.INCOME if line.row.amount.minor > 0 else OperationKind.EXPENSE,
            account_id=self.options.account_id,
            allow_refunds=True,
            refund_candidates=self._refund_candidates,
            tags=self._tags,
            allow_tags=True,
        )
        if line.row.position not in self._drafts:
            day = line.row.occurred_on
            dialog.occurred_on.setDate(QDate(day.year, day.month, day.day))
            dialog.amount.setText(
                format_money(Money(abs(line.row.amount.minor)), include_currency=False)
            )
            dialog.note.setPlainText(line.row.note)
        if dialog.exec() == QDialog.DialogCode.Accepted and dialog.result_draft is not None:
            self._classification_hints[line.row.position] = "Строка уточнена вручную."
            self._drafts[line.row.position] = dialog.result_draft
            self._links.pop(line.row.position, None)
            self._render(line.row.position)
            self._update_actions()

    def link_selected(self) -> None:
        line = self._selected()
        if (
            line is None
            or not line.candidates
            or line.status not in (ImportStatus.NEW, ImportStatus.POSSIBLE)
        ):
            return
        dialog = QDialog(self)
        dialog.setWindowTitle("Связать банковскую строку")
        dialog.resize(700, 300)
        layout = QVBoxLayout(dialog)
        layout.addWidget(
            plain(
                "Выберите ту же операцию по дате, сумме и смыслу. "
                "Связь не меняет её и не добавляет деньги повторно."
            )
        )
        choices = QComboBox()
        choices.addItem("Создать отдельную операцию", None)
        for operation in line.candidates:
            choices.addItem(
                OPERATION_LABELS[operation.draft.kind]
                + " · "
                + operation.draft.occurred_on.strftime("%d.%m.%Y")
                + " · "
                + operation.draft.note[:150],
                operation.id,
            )
        layout.addWidget(choices)
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.button(QDialogButtonBox.StandardButton.Ok).setText("Применить")
        buttons.button(QDialogButtonBox.StandardButton.Cancel).setText("Отмена")
        buttons.accepted.connect(dialog.accept)
        buttons.rejected.connect(dialog.reject)
        layout.addWidget(buttons)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            self._classification_hints[line.row.position] = "Связь уточнена вручную."
            linked = next((op for op in line.candidates if op.id == choices.currentData()), None)
            if linked:
                self._links[line.row.position] = linked
                self._drafts[line.row.position] = linked.draft
            else:
                self._links.pop(line.row.position, None)
            self._render(line.row.position)
            item = self.table.item(self._table_rows[line.row.position], 0)
            assert item is not None
            item.setCheckState(Qt.CheckState.Checked)
            self._update_actions()

    def _validate(self) -> None:
        try:
            self.selections = self._chosen()
            if not self.selections:
                raise ValidationError("Выберите хотя бы одну корректную строку.")
            self.accept()
        except FinanceError as error:
            QMessageBox.warning(self, "Проверьте строки импорта", str(error))
