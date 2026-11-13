"""Редактирование датированных ручных цен в черновике позиции."""

from collections.abc import Callable
from datetime import date

from PySide6.QtCore import QDate
from PySide6.QtWidgets import QDialog, QHBoxLayout, QHeaderView, QPushButton, QVBoxLayout, QWidget

from personal_finance.domain.investment_prices import ManualPrice, PriceUnit
from personal_finance.domain.investments import SecurityKind
from personal_finance.presentation.analytics import _label, format_report_amount
from personal_finance.presentation.deposit_scenarios import date_text
from personal_finance.presentation.inline_forms import present_form
from personal_finance.presentation.investment_editors import ManualPriceEditor
from personal_finance.presentation.recurring_payments import _table
from personal_finance.presentation.tables import replace_table


class ManualPriceHistory(QDialog):
    def __init__(
        self,
        parent: QWidget,
        prices: dict[date, ManualPrice],
        on_changed: Callable[[], None],
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("Ручные цены позиции")
        self.prices = prices
        self.on_changed = on_changed
        self.days: list[date] = []
        layout = QVBoxLayout(self)
        layout.addWidget(_label("Чтобы записать изменения в книгу, сохраните позицию."))
        self.table = _table("Ручные цены по датам")
        self.table.doubleClicked.connect(lambda _: self._edit())
        layout.addWidget(self.table, 1)
        self.status = _label("")
        layout.addWidget(self.status)
        actions = QHBoxLayout()
        self.edit = QPushButton("Изменить цену…")
        self.edit.clicked.connect(self._edit)
        actions.addWidget(self.edit)
        self.remove = QPushButton("Убрать из черновика")
        self.remove.clicked.connect(self._remove)
        actions.addWidget(self.remove)
        actions.addStretch()
        layout.addLayout(actions)
        self._refresh()

    def _selected(self) -> date | None:
        rows = self.table.selectionModel().selectedRows()
        return self.days[rows[0].row()] if rows else None

    def _selection_changed(self) -> None:
        selected = self._selected() is not None
        self.edit.setEnabled(selected)
        self.remove.setEnabled(selected)

    def _refresh(self, focus_day: date | None = None) -> None:
        self.days = sorted(self.prices, reverse=True)
        rows = []
        for day in self.days:
            price = self.prices[day]
            unit = "%" if price.unit is PriceUnit.PERCENT_OF_FACE else price.currency.code
            value = format(price.value, "f").replace(".", ",")
            details = ""
            if price.unpaid_face is not None and price.accrued_per_security is not None:
                nominal = format_report_amount(price.unpaid_face.minor, price.currency)
                accrued = format_report_amount(price.accrued_per_security.minor, price.currency)
                details = f"Номинал {nominal} · НКД {accrued} {price.currency.code}"
            rows.append([date_text(day), f"{value} {unit}", details])
        replace_table(
            self.table,
            ["Дата", "Цена", "Параметры облигации"],
            rows,
            right_columns=(1,),
            selection_changed=self._selection_changed,
        )
        show_bond_details = any(row[2] for row in rows)
        header = self.table.horizontalHeader()
        header.setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(2 if show_bond_details else 1, QHeaderView.ResizeMode.Stretch)
        self.table.setColumnHidden(2, not show_bond_details)
        if focus_day is not None and focus_day in self.prices:
            self.table.selectRow(self.days.index(focus_day))
        self._selection_changed()
        self.status.setText("Ручных цен пока нет." if not rows else "")

    def _edit(self) -> None:
        day = self._selected()
        if day is None:
            return
        original = self.prices[day]
        kind = (
            SecurityKind.BOND if original.unit is PriceUnit.PERCENT_OF_FACE else SecurityKind.STOCK
        )
        dialog = ManualPriceEditor(
            self,
            original.security_id,
            kind,
            QDate(day),
            original,
            occupied_dates=frozenset(self.prices) - {day},
        )

        def accepted(code: int) -> None:
            price = dialog.price
            if code == QDialog.DialogCode.Accepted and price is not None:
                del self.prices[day]
                self.prices[price.quoted_on] = price
                self.on_changed()
                self._refresh(price.quoted_on)

        present_form(dialog, None, accepted)

    def _remove(self) -> None:
        day = self._selected()
        if day is None:
            return
        del self.prices[day]
        self.on_changed()
        self._refresh()
        self.status.setText(f"Цена от {date_text(day)} убрана из черновика.")
