"""Встроенная сводка сохранённых инвестиционных позиций."""

from datetime import date

from PySide6.QtCore import QDate, Signal
from PySide6.QtWidgets import (
    QDialog,
    QFormLayout,
    QHBoxLayout,
    QHeaderView,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from personal_finance.domain.investment_portfolio import PortfolioRow, SavedPortfolioReport
from personal_finance.domain.models import RUB, Currency
from personal_finance.presentation.analytics import _label, format_report_amount
from personal_finance.presentation.deposit_scenarios import date_text
from personal_finance.presentation.inline_forms import present_form
from personal_finance.presentation.investment_editors import SECURITY_LABELS
from personal_finance.presentation.investment_preview import (
    InvestmentPreviewDialog,
    InvestmentPreviewRequest,
)
from personal_finance.presentation.recurring_payments import _date, _date_input, _table
from personal_finance.presentation.tables import replace_table


class InvestmentPortfolioOptions(QDialog):
    calculationRequested = Signal()

    def __init__(self, parent: QWidget) -> None:
        super().__init__(parent)
        self.setWindowTitle("Инвестиционный портфель")
        layout = QVBoxLayout(self)
        layout.addWidget(_label("Бумаги из сохранённых позиций на конец выбранного дня."))
        row = QHBoxLayout()
        form = QFormLayout()
        self.as_of = _date_input(QDate.currentDate(), "Дата оценки портфеля")
        form.addRow("На дату", self.as_of)
        row.addLayout(form)
        self.calculate = QPushButton("Показать портфель")
        self.calculate.setObjectName("primary")
        self.calculate.clicked.connect(self.calculationRequested)
        row.addWidget(self.calculate)
        row.addStretch()
        layout.addLayout(row)
        layout.addStretch()

    @property
    def report_day(self) -> date:
        return _date(self.as_of)


def _amount(minor: int | None, currency: Currency) -> str:
    return "—" if minor is None else format_report_amount(minor, currency)


def _row_amount(minor: int | None, currency: Currency) -> str:
    if minor is None:
        return "—"
    unit = "₽" if currency == RUB else currency.code
    return f"{format_report_amount(minor, currency)} {unit}"


class InvestmentPortfolioDialog(QDialog):
    def __init__(self, report: SavedPortfolioReport, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Инвестиционный портфель")
        self.report = report
        layout = QVBoxLayout(self)
        layout.addWidget(_label(f"На конец {date_text(report.as_of)} · Только ценные бумаги"))
        if report.unavailable_count:
            layout.addWidget(_label(f"Позиций на недоступных счетах: {report.unavailable_count}."))
        if not report.rows:
            layout.addWidget(
                _label(
                    "На эту дату сохранённых сделок и выплат нет. "
                    "Добавьте позицию через «Расчёт инвестиционной позиции…»."
                )
            )
            layout.addStretch()
            return

        self.totals = _table("Итоги портфеля по валютам")
        replace_table(
            self.totals,
            [
                "Валюта",
                "Себест.",
                "Оценка",
                "Без цены",
                "Реализация",
                "Выплаты",
                "Итог",
            ],
            [
                [
                    item.currency.code,
                    _amount(item.cost_minor, item.currency),
                    _amount(item.priced_market_minor, item.currency),
                    str(item.unpriced_count),
                    _amount(item.realized_minor, item.currency),
                    _amount(item.distributions_minor, item.currency),
                    _amount(item.result_minor, item.currency),
                ]
                for item in report.totals
            ],
            right_columns=(1, 2, 3, 4, 5, 6),
        )
        self.totals.setSelectionMode(self.totals.SelectionMode.NoSelection)
        self.totals.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        self.totals.setMaximumHeight(
            self.totals.horizontalHeader().sizeHint().height()
            + min(3, len(report.totals)) * self.totals.verticalHeader().defaultSectionSize()
            + 20
        )
        self.totals.setToolTip(
            "Себест. — себестоимость оставшихся бумаг. "
            "Реализация — результат продаж и погашений за вычетом себестоимости и комиссий."
        )
        layout.addWidget(self.totals)
        if any(item.unpriced_count for item in report.totals):
            layout.addWidget(
                _label("Оценка включает бумаги с ценой. Для общего результата нужны все цены.")
            )

        self.positions = _table("Позиции в инвестиционном портфеле")
        model = replace_table(
            self.positions,
            [
                "Инструмент",
                "Счёт",
                "Бумаг",
                "Себест.",
                "Оценка",
                "Итог",
            ],
            [
                [
                    row.position.security.code,
                    row.position.account.name,
                    str(row.position.quantity),
                    _row_amount(row.position.cost_minor, row.position.security.currency),
                    _row_amount(row.market_minor, row.position.security.currency),
                    _row_amount(
                        row.result.total_minor if row.result else None,
                        row.position.security.currency,
                    ),
                ]
                for row in report.rows
            ],
            right_columns=(2, 3, 4, 5),
        )
        for index, row in enumerate(report.rows):
            security = row.position.security
            kind = SECURITY_LABELS[security.kind]
            model.item(index, 0).setToolTip(f"{kind} · {security.code}")
        header = self.positions.horizontalHeader()
        header.setMinimumSectionSize(header.fontMetrics().horizontalAdvance("Инструмент") + 24)
        header.setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        layout.addWidget(self.positions, 1)
        self.details = QPushButton("Открыть расчёт позиции")
        self.details.setProperty("stickyActions", True)
        self.details.setEnabled(False)
        self.details.clicked.connect(self._open_details)
        self.positions.selectionModel().selectionChanged.connect(self._selection_changed)
        self.positions.doubleClicked.connect(lambda _: self._open_details())
        layout.addWidget(self.details)

    def _selected(self) -> PortfolioRow | None:
        rows = self.positions.selectionModel().selectedRows()
        return self.report.rows[rows[0].row()] if rows else None

    def _selection_changed(self) -> None:
        row = self._selected()
        self.details.setEnabled(row is not None)

    def _open_details(self) -> None:
        row = self._selected()
        if row is None:
            return
        inputs = row.record.inputs
        request = InvestmentPreviewRequest(
            row.position.account, inputs.security, inputs.events, self.report.as_of, inputs.prices
        )
        present_form(InvestmentPreviewDialog(request, row.valuation, self), None)
