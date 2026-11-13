"""Проверка кандидатов из денежного журнала без его изменения."""

from PySide6.QtWidgets import QDialog, QHeaderView, QPushButton, QVBoxLayout, QWidget

from personal_finance.domain.investment_cash_reconciliation import (
    InvestmentCashRow,
    InvestmentCashStatus,
)
from personal_finance.domain.investments import SecurityTrade
from personal_finance.presentation.analytics import _label, format_report_amount
from personal_finance.presentation.analytics_cashflow import CashFlowSourcesDialog
from personal_finance.presentation.deposit_scenarios import date_text
from personal_finance.presentation.inline_forms import present_form
from personal_finance.presentation.investment_editors import DISTRIBUTION_LABELS, TRADE_LABELS
from personal_finance.presentation.recurring_payments import _table
from personal_finance.presentation.tables import replace_table

_STATUS_LABELS = {
    InvestmentCashStatus.NO_MOVEMENT: "Сумма ноль",
    InvestmentCashStatus.MISSING: "Нет совпадений",
    InvestmentCashStatus.UNIQUE: "Один кандидат",
    InvestmentCashStatus.AMBIGUOUS: "Нужно уточнить",
}


class InvestmentCashDialog(QDialog):
    def __init__(
        self,
        rows: tuple[InvestmentCashRow, ...],
        accounts: dict[str, str],
        categories: dict[str, str],
        parent: QWidget,
    ) -> None:
        super().__init__(parent)
        self.rows = rows
        self.accounts, self.categories = accounts, categories
        self.setWindowTitle("Сверка с денежным журналом")
        layout = QVBoxLayout(self)
        layout.addWidget(
            _label(
                "Кандидаты совпадают по счёту, дате, валюте и движению с учётом комиссии и НКД. "
                "Даже один кандидат нужно сверить с брокерским отчётом."
            )
        )
        self.table = _table("Сверка инвестиционных событий")
        replace_table(
            self.table,
            ["Дата", "Код", "Счёт", "Событие", "Сумма", "Сверка"],
            [
                [
                    date_text(row.event.occurred_on),
                    row.security_code,
                    row.account.name,
                    (
                        TRADE_LABELS[row.event.kind]
                        if isinstance(row.event, SecurityTrade)
                        else DISTRIBUTION_LABELS[row.event.kind]
                    ),
                    f"{format_report_amount(row.minor, row.currency)} {row.currency.code}",
                    _STATUS_LABELS[row.status],
                ]
                for row in rows
            ],
            right_columns=(4,),
        )
        header = self.table.horizontalHeader()
        header.setMinimumSectionSize(header.fontMetrics().horizontalAdvance("Счёт") + 22)
        header.setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)
        layout.addWidget(self.table, 1)
        self.open_sources = QPushButton("Показать операции журнала")
        self.open_sources.setProperty("stickyActions", True)
        self.open_sources.setEnabled(False)
        self.open_sources.clicked.connect(self._open_sources)
        self.table.doubleClicked.connect(lambda _: self._open_sources())
        self.table.selectionModel().selectionChanged.connect(self._selection_changed)
        layout.addWidget(self.open_sources)

    def _selected(self) -> InvestmentCashRow | None:
        rows = self.table.selectionModel().selectedRows()
        return self.rows[rows[0].row()] if rows else None

    def _selection_changed(self) -> None:
        row = self._selected()
        self.open_sources.setEnabled(row is not None and bool(row.candidates))

    def _open_sources(self) -> None:
        row = self._selected()
        if row is None or not row.candidates:
            return
        dialog = CashFlowSourcesDialog(
            row.candidates,
            row.currency,
            "Кандидаты из денежного журнала",
            self.accounts,
            self.categories,
            self,
        )
        present_form(dialog, None)
