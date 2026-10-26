"""Русские формы ручных финансовых операций и просмотра истории."""

from datetime import date

from PySide6.QtCore import QDate
from PySide6.QtWidgets import (
    QComboBox,
    QDateEdit,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPlainTextEdit,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from personal_finance.domain.errors import FinanceError
from personal_finance.domain.ledger import (
    OPERATION_LABELS,
    Operation,
    OperationDraft,
    OperationKind,
)
from personal_finance.domain.models import (
    ACCOUNT_LABELS,
    Account,
    Category,
    CategoryKind,
    Institution,
    Money,
)


def format_money(value: Money, *, include_currency: bool = True) -> str:
    whole, fractional = divmod(abs(value.minor), 10**value.currency.exponent)
    result = ("-" if value.minor < 0 else "") + f"{whole:,}".replace(",", " ")
    if value.currency.exponent:
        result += "," + str(fractional).zfill(value.currency.exponent)
    return result + (" " + value.currency.code if include_currency else "")


def category_title(category: Category, categories: list[Category]) -> str:
    by_id = {value.id: value for value in categories}
    names = [category.name]
    visited = {category.id}
    parent_id = category.parent_id
    while parent_id is not None and parent_id in by_id and parent_id not in visited:
        visited.add(parent_id)
        parent = by_id[parent_id]
        names.append(parent.name)
        parent_id = parent.parent_id
    return " / ".join(reversed(names))


class OperationDialog(QDialog):
    def __init__(
        self,
        accounts: list[Account],
        categories: list[Category],
        institutions: list[Institution],
        parent: QWidget | None = None,
        *,
        existing: Operation | None = None,
        kind: OperationKind = OperationKind.EXPENSE,
        account_id: str | None = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("Исправить операцию" if existing else "Добавить операцию")
        self.setMinimumWidth(650)
        self.result_draft: OperationDraft | None = None
        self._categories = categories
        layout = QVBoxLayout(self)
        introduction = QLabel(
            "Исправление создаст новую версию. Предыдущая версия останется в истории."
            if existing
            else "Операция изменяет остатки на указанную дату. "
            "Перевод между своими счетами не является доходом."
        )
        introduction.setWordWrap(True)
        layout.addWidget(introduction)
        self.form = QFormLayout()
        self.kind = QComboBox()
        for value, title in OPERATION_LABELS.items():
            self.kind.addItem(title, value.value)
        self.occurred_on = QDateEdit(QDate.currentDate())
        self.occurred_on.setCalendarPopup(True)
        self.occurred_on.setDisplayFormat("dd.MM.yyyy")
        self.occurred_on.setMinimumDate(QDate(1, 1, 1))
        self.account = QComboBox()
        self.destination = QComboBox()
        banks = {bank.id: bank.name for bank in institutions}
        for combo in (self.account, self.destination):
            combo.addItem("Выберите счёт", None)
            for account in accounts:
                detail = banks.get(account.institution_id or "", ACCOUNT_LABELS[account.kind])
                combo.addItem(f"{account.name} · {detail} · {account.currency}", account.id)
        self.amount = QLineEdit()
        self.category = QComboBox()
        self.fee = QLineEdit("0,00")
        self.fee_category = QComboBox()
        self.fee_category.addItem("Без комиссии", None)
        for category in categories:
            if category.kind == CategoryKind.EXPENSE:
                self.fee_category.addItem(category_title(category, categories), category.id)
        self.note = QPlainTextEdit()
        self.note.setMaximumHeight(85)
        self.form.addRow("Вид операции", self.kind)
        self.form.addRow("Дата", self.occurred_on)
        self.form.addRow("Счёт", self.account)
        self.form.addRow("Сумма, RUB", self.amount)
        self.form.addRow("Категория", self.category)
        self.form.addRow("Счёт получения", self.destination)
        self.form.addRow("Комиссия, RUB", self.fee)
        self.form.addRow("Категория комиссии", self.fee_category)
        self.form.addRow("Примечание", self.note)
        layout.addLayout(self.form)
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.button(QDialogButtonBox.StandardButton.Save).setText("Сохранить")
        buttons.button(QDialogButtonBox.StandardButton.Cancel).setText("Отмена")
        buttons.accepted.connect(self._validate)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)
        self.kind.currentIndexChanged.connect(self._update_fields)
        self.kind.setCurrentIndex(
            self.kind.findData((existing.draft.kind if existing else kind).value)
        )
        self._update_fields()
        if existing is not None:
            draft = existing.draft
            self.occurred_on.setDate(
                QDate(draft.occurred_on.year, draft.occurred_on.month, draft.occurred_on.day)
            )
            self.account.setCurrentIndex(self.account.findData(draft.account_id))
            self.amount.setText(format_money(draft.amount, include_currency=False))
            self.category.setCurrentIndex(self.category.findData(draft.category_id))
            self.destination.setCurrentIndex(
                self.destination.findData(draft.destination_account_id)
            )
            self.fee.setText(format_money(draft.fee, include_currency=False))
            self.fee_category.setCurrentIndex(self.fee_category.findData(draft.fee_category_id))
            self.note.setPlainText(draft.note)
        elif account_id is not None:
            self.account.setCurrentIndex(self.account.findData(account_id))

    def _update_fields(self) -> None:
        kind = OperationKind(self.kind.currentData())
        transfer = kind == OperationKind.TRANSFER
        self.form.setRowVisible(
            self.category, kind in (OperationKind.INCOME, OperationKind.EXPENSE)
        )
        for widget in (self.destination, self.fee, self.fee_category):
            self.form.setRowVisible(widget, transfer)
        self.category.clear()
        self.category.addItem("Выберите категорию", None)
        required = CategoryKind.INCOME if kind == OperationKind.INCOME else CategoryKind.EXPENSE
        for category in self._categories:
            if category.kind == required:
                self.category.addItem(category_title(category, self._categories), category.id)
        self.amount.setPlaceholderText(
            "Можно указать ноль или отрицательный остаток"
            if kind == OperationKind.OPENING
            else "Например, 1 234,56"
        )

    def _validate(self) -> None:
        try:
            kind = OperationKind(self.kind.currentData())
            selected = self.account.currentData()
            if selected is None:
                raise FinanceError("Выберите счёт операции.")
            qdate = self.occurred_on.date()
            self.result_draft = OperationDraft(
                kind,
                date(qdate.year(), qdate.month(), qdate.day()),
                selected,
                Money.parse(self.amount.text()),
                category_id=self.category.currentData()
                if kind in (OperationKind.INCOME, OperationKind.EXPENSE)
                else None,
                destination_account_id=self.destination.currentData()
                if kind == OperationKind.TRANSFER
                else None,
                fee=Money.parse(self.fee.text()) if kind == OperationKind.TRANSFER else Money(0),
                fee_category_id=self.fee_category.currentData()
                if kind == OperationKind.TRANSFER
                else None,
                note=self.note.toPlainText(),
            )
        except FinanceError as exc:
            QMessageBox.warning(self, "Проверьте операцию", str(exc))
            return
        self.accept()


class OperationHistoryDialog(QDialog):
    def __init__(self, versions: list[Operation], parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("История финансовой операции")
        self.resize(980, 450)
        layout = QVBoxLayout(self)
        explanation = QLabel(
            "Только последняя принятая версия влияет на остатки. Удаление сохраняет историю."
        )
        explanation.setWordWrap(True)
        layout.addWidget(explanation)
        table = QTableWidget(len(versions), 6)
        table.setHorizontalHeaderLabels(
            ["Версия", "Дата", "Вид", "Сумма", "Состояние", "Примечание"]
        )
        table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        table.verticalHeader().hide()
        table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
        table.horizontalHeader().setSectionResizeMode(5, QHeaderView.ResizeMode.Stretch)
        for row, operation in enumerate(versions):
            values = [
                str(row + 1),
                operation.draft.occurred_on.strftime("%d.%m.%Y"),
                OPERATION_LABELS[operation.draft.kind],
                format_money(operation.draft.amount),
                "Удалена" if operation.is_deleted else ("Создана" if row == 0 else "Исправлена"),
                operation.draft.note,
            ]
            for column, value in enumerate(values):
                table.setItem(row, column, QTableWidgetItem(value))
        layout.addWidget(table)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        buttons.button(QDialogButtonBox.StandardButton.Close).setText("Закрыть")
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)
