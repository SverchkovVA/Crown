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

from personal_finance.domain.financial_assets import FinancialAssetTotal
from personal_finance.domain.investment_cash_reconciliation import InvestmentCashRow
from personal_finance.domain.investment_portfolio import (
    PortfolioRow,
    SavedPortfolioReport,
    portfolio_allocation,
)
from personal_finance.domain.models import RUB, Currency
from personal_finance.presentation.analytics import _label, format_report_amount
from personal_finance.presentation.deposit_scenarios import date_text
from personal_finance.presentation.financial_assets import FinancialAssetsDialog
from personal_finance.presentation.inline_forms import present_form
from personal_finance.presentation.investment_cash_reconciliation import InvestmentCashDialog
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
    exportRequested = Signal()

    def __init__(
        self,
        report: SavedPortfolioReport,
        parent: QWidget | None = None,
        *,
        reconciliation: tuple[InvestmentCashRow, ...] = (),
        assets: tuple[FinancialAssetTotal, ...] = (),
        account_names: dict[str, str] | None = None,
        category_names: dict[str, str] | None = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("Инвестиционный портфель")
        self.report = report
        self.reconciliation = reconciliation
        self.assets = assets
        self.account_names = account_names or {}
        self.category_names = category_names or {}
        layout = QVBoxLayout(self)
        title_row = QHBoxLayout()
        title_row.addWidget(_label(f"На конец {date_text(report.as_of)} · Только ценные бумаги"), 1)
        self.show_allocation = QPushButton("Структура портфеля")
        self.show_allocation.clicked.connect(self._open_allocation)
        self.show_allocation.setVisible(any(row.position.quantity for row in report.rows))
        title_row.addWidget(self.show_allocation)
        layout.addLayout(title_row)
        if report.unavailable_count:
            layout.addWidget(_label(f"Позиций на недоступных счетах: {report.unavailable_count}."))
        self.show_assets = QPushButton("Деньги и бумаги")
        self.show_assets.clicked.connect(self._open_assets)
        self.show_assets.setVisible(bool(assets))
        if not report.rows:
            layout.addWidget(
                _label(
                    "На эту дату сохранённых сделок и выплат нет. "
                    "Добавьте позицию через «Расчёт инвестиционной позиции…»."
                )
            )
            layout.addWidget(self.show_assets)
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
        self.details.setEnabled(False)
        self.details.clicked.connect(self._open_details)
        self.positions.selectionModel().selectionChanged.connect(self._selection_changed)
        self.positions.doubleClicked.connect(lambda _: self._open_details())
        actions = QWidget()
        actions.setProperty("stickyActions", True)
        footer = QHBoxLayout(actions)
        footer.setContentsMargins(0, 0, 0, 0)
        footer.addWidget(self.details)
        self.check_cash = QPushButton("Сверить с журналом")
        self.check_cash.setVisible(bool(reconciliation))
        self.check_cash.clicked.connect(self._open_reconciliation)
        footer.addWidget(self.check_cash)
        footer.addWidget(self.show_assets)
        self.export = QPushButton("Экспорт…")
        self.export.clicked.connect(self.exportRequested)
        footer.addWidget(self.export)
        layout.addWidget(actions)

    def _open_allocation(self) -> None:
        present_form(PortfolioAllocationDialog(self.report, self), None)

    def _open_assets(self) -> None:
        dialog = FinancialAssetsDialog(
            self.report.as_of, self.assets, self.report.unavailable_count, self
        )
        present_form(dialog, None)

    def _open_reconciliation(self) -> None:
        dialog = InvestmentCashDialog(
            self.reconciliation, self.account_names, self.category_names, self
        )
        present_form(dialog, None)

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


class PortfolioAllocationDialog(QDialog):
    def __init__(self, report: SavedPortfolioReport, parent: QWidget) -> None:
        super().__init__(parent)
        self.setWindowTitle("Структура портфеля")
        layout = QVBoxLayout(self)
        layout.addWidget(
            _label(
                f"Открытые позиции на конец {date_text(report.as_of)}. "
                "Доли рассчитаны отдельно для каждой валюты."
            )
        )
        rows = portfolio_allocation(report)
        if report.unavailable_count or any(row.unpriced_count for row in rows):
            layout.addWidget(
                _label(
                    "Для расчёта долей нужны все позиции и цены. "
                    "В оценку вошли только бумаги с ценой."
                )
            )
        if any(
            item.open_count and not item.unpriced_count and item.priced_market_minor == 0
            for item in report.totals
        ):
            layout.addWidget(_label("При нулевой оценке всей валюты доли не рассчитываются."))
        self.table = _table("Структура портфеля по видам ценных бумаг")
        replace_table(
            self.table,
            ["Вид бумаг", "Валюта", "Позиций", "Оценка", "Без цены", "Доля"],
            [
                [
                    SECURITY_LABELS[row.kind],
                    row.currency.code,
                    str(row.open_count),
                    _amount(row.priced_market_minor, row.currency),
                    str(row.unpriced_count),
                    (
                        "—"
                        if row.share_basis_points is None
                        else f"{format_report_amount(row.share_basis_points, RUB)} %"
                    ),
                ]
                for row in rows
            ],
            right_columns=(2, 3, 4, 5),
        )
        self.table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        layout.addWidget(self.table, 1)
