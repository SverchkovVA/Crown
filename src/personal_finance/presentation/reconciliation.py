"""Ввод контрольного остатка и просмотр результата сверки."""

from datetime import date

from PySide6.QtCore import QDate, Qt
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
    QTableView,
    QVBoxLayout,
    QWidget,
)

from personal_finance.domain.errors import FinanceError
from personal_finance.domain.ledger import OPERATION_LABELS
from personal_finance.domain.models import Account, Money
from personal_finance.domain.reconciliation import Reconciliation
from personal_finance.presentation.money import format_minor, format_money
from personal_finance.presentation.tables import replace_table


class ReconciliationInputDialog(QDialog):
    def __init__(
        self,
        accounts: list[Account],
        as_of: date,
        parent: QWidget | None = None,
        *,
        account_id: str | None = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("Сверить остаток")
        self.setMinimumWidth(550)
        self.result_request: tuple[str, date, Money] | None = None
        layout = QVBoxLayout(self)
        text = QLabel(
            "Введите остаток на конец дня из выписки или другого контрольного источника. "
            "Сверка покажет разницу и не изменит операции."
        )
        text.setWordWrap(True)
        layout.addWidget(text)
        form = QFormLayout()
        self.account = QComboBox()
        self.account.addItem("Выберите счёт", None)
        for account in accounts:
            if account.currency == "RUB":
                self.account.addItem(account.name + " · RUB", account.id)
        self.as_of = QDateEdit(QDate(as_of.year, as_of.month, as_of.day))
        self.as_of.setMinimumDate(QDate(1, 1, 1))
        self.as_of.setDisplayFormat("dd.MM.yyyy")
        self.as_of.setCalendarPopup(True)
        self.statement = QLineEdit()
        self.statement.setPlaceholderText("Допускаются ноль и отрицательный остаток")
        form.addRow("Счёт", self.account)
        form.addRow("На конец даты", self.as_of)
        form.addRow("Остаток из выписки, RUB", self.statement)
        layout.addLayout(form)
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.button(QDialogButtonBox.StandardButton.Ok).setText("Сравнить")
        buttons.button(QDialogButtonBox.StandardButton.Cancel).setText("Отмена")
        buttons.accepted.connect(self._validate)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)
        if account_id:
            self.account.setCurrentIndex(max(0, self.account.findData(account_id)))
        elif self.account.count() == 2:
            self.account.setCurrentIndex(1)

    def _validate(self) -> None:
        self.result_request = None
        try:
            account_id = self.account.currentData()
            if account_id is None:
                raise FinanceError("Выберите счёт для сверки.")
            value = self.as_of.date()
            self.result_request = (
                account_id,
                date(value.year(), value.month(), value.day()),
                Money.parse(self.statement.text()),
            )
        except FinanceError as exc:
            QMessageBox.warning(self, "Проверьте сверку", str(exc))
            return
        self.accept()


class ReconciliationResultDialog(QDialog):
    def __init__(
        self, result: Reconciliation, account_name: str, parent: QWidget | None = None
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("Результат сверки")
        self.resize(900, 600)
        self.result_snapshot = result
        layout = QVBoxLayout(self)
        title = QLabel(account_name + " · на конец " + result.as_of.strftime("%d.%m.%Y"))
        title.setTextFormat(Qt.TextFormat.PlainText)
        title.setWordWrap(True)
        layout.addWidget(title)
        amount = format_minor(result.difference_minor, result.book_balance.currency)
        self.summary = QLabel(
            "По книге: " + format_money(result.book_balance) + "\n"
            "Из выписки: "
            + format_money(result.statement_balance)
            + "\n"
            + (
                "Остатки совпадают."
                if result.matches
                else "В выписке больше на " + amount
                if result.difference_minor > 0
                else "В выписке меньше на "
                + format_minor(-result.difference_minor, result.book_balance.currency)
            )
        )
        self.summary.setWordWrap(True)
        layout.addWidget(self.summary)
        info = QLabel(
            "Ниже — принятые операции этого счёта, учтённые в остатке. "
            "Сравните их с выпиской. Сверка ничего не записывает и не подгоняет остаток; "
            "результат относится к снимку книги на момент расчёта."
        )
        info.setWordWrap(True)
        layout.addWidget(info)
        self.table = QTableView()
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
        self.table.horizontalHeader().setStretchLastSection(True)
        self.model = replace_table(
            self.table,
            ["Дата", "Вид", "Изменение остатка", "Примечание"],
            [
                [
                    line.operation.draft.occurred_on.strftime("%d.%m.%Y"),
                    OPERATION_LABELS[line.operation.draft.kind],
                    format_money(line.change),
                    line.operation.draft.note,
                ]
                for line in result.lines
            ],
            right_columns=(2,),
        )
        layout.addWidget(self.table, 1)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        buttons.button(QDialogButtonBox.StandardButton.Close).setText("Закрыть")
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)
