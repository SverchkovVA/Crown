"""Состояние денежных средств с доступом к исходным движениям."""

from datetime import date

from PySide6.QtCore import QDate, Signal
from PySide6.QtWidgets import (
    QDateEdit,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QHeaderView,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from personal_finance.domain.account_position import AccountPosition
from personal_finance.domain.models import ACCOUNT_LABELS, Category
from personal_finance.presentation.analytics import _label, format_report_amount
from personal_finance.presentation.analytics_cashflow import CashFlowSourcesDialog, _size, _view
from personal_finance.presentation.inline_forms import present_form
from personal_finance.presentation.ledger_dialogs import category_title
from personal_finance.presentation.tables import replace_table


class AccountPositionOptions(QDialog):
    calculationRequested = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Средства на счетах · Дата")
        self.result_request: date | None = None
        layout = QVBoxLayout(self)
        layout.addWidget(_label("Остатки по принятым операциям на конец выбранного дня."))
        form = QFormLayout()
        self.as_of = QDateEdit()
        self.as_of.setDateRange(QDate(1, 1, 1), QDate(9999, 12, 31))
        self.as_of.setDate(QDate.currentDate())
        self.as_of.setDisplayFormat("dd.MM.yyyy")
        self.as_of.setCalendarPopup(True)
        self.as_of.setAccessibleName("Дата состояния счетов")
        form.addRow("На дату включительно", self.as_of)
        layout.addLayout(form)
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        self.calculate = buttons.button(QDialogButtonBox.StandardButton.Ok)
        self.calculate.setText("Рассчитать")
        buttons.button(QDialogButtonBox.StandardButton.Cancel).setText("Отмена")
        buttons.accepted.connect(self._calculate)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def _calculate(self) -> None:
        value = self.as_of.date()
        self.result_request = date(value.year(), value.month(), value.day())
        self.calculationRequested.emit()


class AccountPositionDialog(QDialog):
    def __init__(
        self, report: AccountPosition, categories: list[Category], parent: QWidget | None = None
    ) -> None:
        super().__init__(parent)
        self.report = report
        self._account_names = {row.account.id: row.account.name for row in report.accounts}
        self._category_names = {c.id: category_title(c, categories) for c in categories}
        self.setWindowTitle("Средства на счетах")
        _size(self, 1000, 650)
        layout = QVBoxLayout(self)
        layout.addWidget(
            _label(f"На конец {report.as_of:%d.%m.%Y} · Счета: {len(report.accounts)}")
        )
        layout.addWidget(
            _label(
                "Показаны деньги по журналу, отдельно по валютам. "
                "Банковские счета и наличные включают накопительные счета. "
                "Деньги на инвестиционных счетах показаны отдельно от них. "
                "Стоимость ценных бумаг и прочего имущества в этот отчёт не входит."
            )
        )
        self.totals = _view("Итоги средств по валютам")
        replace_table(
            self.totals,
            [
                "Валюта",
                "Банковские счета\nи наличные",
                "Деньги на инвестиционных\nсчетах",
                "Всего денег\nна счетах",
            ],
            [
                [
                    row.currency.code,
                    format_report_amount(row.bank_and_cash_minor, row.currency),
                    format_report_amount(row.investment_cash_minor, row.currency),
                    format_report_amount(row.total_minor, row.currency),
                ]
                for row in report.totals
            ],
            right_columns=(1, 2, 3),
        )
        self.totals.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self.totals.setMaximumHeight(
            self.totals.horizontalHeader().sizeHint().height()
            + max(1, min(4, len(report.totals))) * self.totals.verticalHeader().defaultSectionSize()
            + self.totals.frameWidth() * 2
            + 6
        )
        layout.addWidget(self.totals)
        if not report.accounts:
            layout.addWidget(_label("В книге пока нет счетов. Добавьте счёт в разделе «Счета»."))
        self.accounts = _view("Средства по каждому счёту")
        replace_table(
            self.accounts,
            ["Счёт", "Тип", "Валюта", "Остаток"],
            [
                [
                    row.account.name,
                    ACCOUNT_LABELS[row.account.kind],
                    row.currency.code,
                    format_report_amount(row.minor, row.currency),
                ]
                for row in report.accounts
            ],
            right_columns=(3,),
        )
        self.accounts.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        layout.addWidget(self.accounts, 2)
        layout.addWidget(
            _label(
                "Нулевые счета включены. Отрицательные остатки показаны со знаком. "
                "Это снимок журнала: для обновления вернитесь к дате и рассчитайте заново."
            )
        )
        self.open_sources = QPushButton("Движения выбранного счёта")
        self.open_sources.setEnabled(False)
        self.accounts.selectionModel().selectionChanged.connect(self._update_actions)
        self.accounts.doubleClicked.connect(lambda _: self._open_sources())
        self.open_sources.clicked.connect(self._open_sources)
        layout.addWidget(self.open_sources)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        buttons.button(QDialogButtonBox.StandardButton.Close).setText("К дате расчёта")
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def _selected(self) -> int | None:
        rows = self.accounts.selectionModel().selectedRows()
        return rows[0].row() if len(rows) == 1 else None

    def _update_actions(self) -> None:
        row = self._selected()
        self.open_sources.setEnabled(
            row is not None
            and any(
                line.account_id == self.report.accounts[row].account.id
                for line in self.report.sources
            )
        )

    def _open_sources(self) -> None:
        index = self._selected()
        if index is None:
            return
        row = self.report.accounts[index]
        lines = tuple(line for line in self.report.sources if line.account_id == row.account.id)
        if not lines:
            return
        dialog = CashFlowSourcesDialog(
            lines,
            row.currency,
            f"Движения счёта «{row.account.name}» по {self.report.as_of:%d.%m.%Y}",
            self._account_names,
            self._category_names,
            self,
        )
        present_form(dialog, None)
