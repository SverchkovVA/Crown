"""Сводка активов в том же срезе, что и инвестиционный портфель."""

from datetime import date

from PySide6.QtWidgets import QDialog, QHeaderView, QVBoxLayout, QWidget

from personal_finance.domain.financial_assets import FinancialAssetTotal
from personal_finance.presentation.analytics import _label, format_report_amount
from personal_finance.presentation.deposit_scenarios import date_text
from personal_finance.presentation.recurring_payments import _table
from personal_finance.presentation.tables import replace_table


class FinancialAssetsDialog(QDialog):
    def __init__(
        self,
        as_of: date,
        totals: tuple[FinancialAssetTotal, ...],
        unavailable_count: int,
        parent: QWidget,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("Деньги и ценные бумаги")
        layout = QVBoxLayout(self)
        layout.addWidget(_label(f"На конец {date_text(as_of)}"))
        if not totals:
            layout.addWidget(_label("На эту дату нет счетов и сохранённых позиций."))
            layout.addStretch()
            return

        self.totals = _table("Деньги и ценные бумаги по валютам")
        self.totals.setSelectionMode(self.totals.SelectionMode.NoSelection)
        self.totals.setToolTip(
            "Деньги — остатки банковских, накопительных счетов и наличных. "
            "У брокеров — деньги инвестиционных счетов по журналу. "
            "Бумаги — только оценённые позиции. Итог включает деньги и стоимость бумаг."
        )
        replace_table(
            self.totals,
            ["Валюта", "Деньги", "У брокеров", "Бумаги", "Без цены", "Итог"],
            [
                [
                    total.currency.code,
                    format_report_amount(total.liquid_minor, total.currency),
                    format_report_amount(total.investment_cash_minor, total.currency),
                    "—"
                    if total.securities_minor is None
                    else format_report_amount(total.securities_minor, total.currency),
                    str(total.unpriced_count),
                    "—"
                    if total.total_minor is None
                    else format_report_amount(total.total_minor, total.currency),
                ]
                for total in totals
            ],
            right_columns=(1, 2, 3, 4, 5),
        )
        self.totals.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        layout.addWidget(self.totals, 1)
        if any(row.unpriced_count for row in totals):
            layout.addWidget(_label("Итог доступен, когда у всех открытых позиций есть цена."))
        if unavailable_count:
            layout.addWidget(_label(f"Позиций на недоступных счетах: {unavailable_count}."))
        layout.addWidget(
            _label(
                "Остатки взяты из журнала. Оценка бумаг — из портфеля. "
                "Другие активы и обязательства здесь не учитываются."
            )
        )
