"""Встроенный расчёт инвестиционной позиции без записи в книгу."""

from collections.abc import Sequence
from dataclasses import dataclass, replace
from datetime import date
from uuid import uuid4

from PySide6.QtCore import QDate, Qt, Signal
from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QFormLayout,
    QHBoxLayout,
    QHeaderView,
    QLineEdit,
    QPushButton,
    QTableView,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from personal_finance.domain.errors import ValidationError
from personal_finance.domain.investment_prices import InvestmentValuation, ManualPrice, PriceUnit
from personal_finance.domain.investments import (
    PortfolioEvent,
    Security,
    SecurityKind,
    SecurityTrade,
)
from personal_finance.domain.models import RUB, Account
from personal_finance.presentation.analytics import _label, format_report_amount
from personal_finance.presentation.deposit_scenarios import date_text
from personal_finance.presentation.inline_forms import present_form
from personal_finance.presentation.investment_editors import (
    DISTRIBUTION_LABELS,
    MAX_UI_INTEGER,
    SECURITY_LABELS,
    TRADE_LABELS,
    InvestmentEventEditor,
    ManualPriceEditor,
)
from personal_finance.presentation.recurring_payments import _date, _date_input, _table
from personal_finance.presentation.tables import replace_table


@dataclass(frozen=True, slots=True)
class InvestmentPreviewRequest:
    account: Account
    security: Security
    events: tuple[PortfolioEvent, ...]
    as_of: date
    prices: tuple[ManualPrice, ...]


class InvestmentPreviewOptions(QDialog):
    calculationRequested = Signal()

    def __init__(self, parent: QWidget, accounts: Sequence[Account]) -> None:
        super().__init__(parent)
        self.setWindowTitle("Расчёт инвестиционной позиции")
        self.accounts = {value.id: value for value in accounts}
        self.security_id = str(uuid4())
        self.events: list[PortfolioEvent] = []
        self.price: ManualPrice | None = None
        self.request: InvestmentPreviewRequest | None = None
        layout = QVBoxLayout(self)
        fields = QFormLayout()
        layout.addLayout(fields)
        self.account = QComboBox()
        self.account.setAccessibleName("Инвестиционный счёт")
        for account in sorted(accounts, key=lambda value: value.name.casefold()):
            self.account.addItem(account.name, account.id)
        fields.addRow("Инвестиционный счёт", self.account)
        self.kind = QComboBox()
        self.kind.setAccessibleName("Вид инструмента")
        for kind, name in SECURITY_LABELS.items():
            self.kind.addItem(name, kind.value)
        fields.addRow("Вид инструмента", self.kind)
        self.code = QLineEdit()
        self.code.setMaxLength(32)
        self.code.setAccessibleName("Код инструмента")
        fields.addRow("Код инструмента", self.code)
        self.as_of = _date_input(QDate.currentDate(), "Дата расчёта")
        fields.addRow("Дата расчёта", self.as_of)
        self.table = _table("Сделки и выплаты позиции")
        layout.addWidget(self.table, 1)
        actions = QHBoxLayout()
        actions.setSpacing(6)
        self.add = QPushButton("Добавить сделку или выплату")
        self.edit = QPushButton("Изменить")
        self.remove = QPushButton("Убрать")
        self.add.clicked.connect(lambda: self._edit_event(None))
        self.edit.clicked.connect(lambda: self._edit_event(self._selected_row()))
        self.remove.clicked.connect(self._remove_event)
        for item in (self.add, self.edit, self.remove):
            actions.addWidget(item)
        actions.addStretch()
        layout.addLayout(actions)
        prices = QHBoxLayout()
        prices.setSpacing(6)
        self.price_status = _label("")
        prices.addWidget(self.price_status, 1)
        self.set_price = QPushButton("Ручная цена…")
        self.set_price.clicked.connect(self._edit_price)
        prices.addWidget(self.set_price)
        self.remove_price = QPushButton("Убрать цену")
        self.remove_price.clicked.connect(self._remove_price)
        prices.addWidget(self.remove_price)
        layout.addLayout(prices)
        layout.addWidget(_label("Введённые данные хранятся в этой форме."))
        self.feedback = _label("")
        self.feedback.setObjectName("feedbackError")
        self.feedback.hide()
        layout.addWidget(self.feedback)
        self.calculate = QPushButton("Рассчитать")
        self.calculate.setObjectName("primary")
        self.calculate.clicked.connect(self._calculate)
        layout.addWidget(self.calculate)
        self._refresh()

    def _selected_row(self) -> int | None:
        selected = self.table.selectionModel().selectedRows()
        return selected[0].row() if selected else None

    def _selection_changed(self) -> None:
        selected = self._selected_row() is not None
        self.edit.setEnabled(selected)
        self.remove.setEnabled(selected)

    def _refresh(self) -> None:
        rows = []
        for item in self.events:
            if isinstance(item, SecurityTrade):
                name, quantity, amount = (
                    TRADE_LABELS[item.kind],
                    str(item.quantity),
                    item.cash_delta_minor,
                )
            else:
                name, quantity, amount = DISTRIBUTION_LABELS[item.kind], "—", item.received.minor
            rows.append(
                [
                    date_text(item.occurred_on),
                    str(item.sequence),
                    name,
                    quantity,
                    format_report_amount(amount, RUB),
                ]
            )
        replace_table(
            self.table,
            ["Дата", "Порядок", "Вид", "Бумаг", "Движение, ₽"],
            rows,
            right_columns=(1, 3, 4),
            selection_changed=self._selection_changed,
        )
        self.table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)
        self._selection_changed()
        self.kind.setEnabled(not self.events and self.price is None)
        self.calculate.setEnabled(bool(self.events))
        self.price_status.setText(
            "Ручная цена не указана"
            if self.price is None
            else f"Ручная цена от {date_text(self.price.quoted_on)}"
        )
        self.remove_price.setVisible(self.price is not None)
        self.feedback.hide()

    def _edit_event(self, row: int | None) -> None:
        account = self.accounts.get(self.account.currentData())
        if account is None:
            return
        current = self.events[row] if row is not None else None
        sequence = min(
            MAX_UI_INTEGER, max((value.sequence for value in self.events), default=0) + 1
        )
        dialog = InvestmentEventEditor(
            self,
            account,
            self.security_id,
            SecurityKind(self.kind.currentData()),
            self.as_of.date(),
            sequence,
            current,
        )

        def accepted(code: int) -> None:
            if code == QDialog.DialogCode.Accepted and dialog.entry is not None:
                if row is None:
                    self.events.append(dialog.entry)
                else:
                    self.events[row] = dialog.entry
                self._refresh()

        present_form(dialog, None, accepted)

    def _remove_event(self) -> None:
        row = self._selected_row()
        if row is not None:
            del self.events[row]
            self._refresh()

    def _edit_price(self) -> None:
        dialog = ManualPriceEditor(
            self,
            self.security_id,
            SecurityKind(self.kind.currentData()),
            self.as_of.date(),
            self.price,
        )

        def accepted(code: int) -> None:
            if code == QDialog.DialogCode.Accepted and dialog.price is not None:
                self.price = dialog.price
                self._refresh()

        present_form(dialog, None, accepted)

    def _remove_price(self) -> None:
        self.price = None
        self._refresh()

    def _calculate(self) -> None:
        self.request = None
        account = self.accounts.get(self.account.currentData())
        if account is None or not self.events:
            return
        try:
            code = self.code.text()
            security = Security(self.security_id, code, code, SecurityKind(self.kind.currentData()))
        except ValidationError as error:
            self.feedback.setText(str(error))
            self.feedback.show()
            return
        self.feedback.hide()
        self.request = InvestmentPreviewRequest(
            account,
            security,
            tuple(replace(value, account_id=account.id) for value in self.events),
            _date(self.as_of),
            () if self.price is None else (self.price,),
        )
        self.calculationRequested.emit()


class InvestmentPreviewDialog(QDialog):
    def __init__(
        self,
        request: InvestmentPreviewRequest,
        valuation: InvestmentValuation,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("Инвестиционная позиция")
        self.valuation = valuation
        layout = QVBoxLayout(self)
        heading = _label(
            f"{request.security.code} · {request.account.name} · {date_text(request.as_of)}"
        )
        heading.setTextFormat(Qt.TextFormat.PlainText)
        layout.addWidget(heading)
        self.summary = _label("")
        layout.addWidget(self.summary)
        portfolio = valuation.portfolio
        if not portfolio.positions:
            self.summary.setText("На выбранную дату сделок и выплат нет.")
            layout.addStretch()
            return
        (position,) = portfolio.positions
        self.summary.setText(
            f"Бумаг: {position.quantity} · Стоимость партий {self._rub(position.cost_minor)} · "
            f"Результат продаж и погашений {self._rub(position.realized_minor)} · "
            f"Выплаты {self._rub(position.distributions_minor)}"
        )
        if valuation.positions:
            (valued,) = valuation.positions
            if valued.price is None or valued.market_minor is None:
                layout.addWidget(_label("Ручной цены на дату расчёта нет."))
            else:
                price = valued.price
                price_text = format(price.value, "f").replace(".", ",")
                source = f"{price_text} ₽ за бумагу"
                if price.unit is PriceUnit.PERCENT_OF_FACE:
                    assert price.unpaid_face is not None and price.accrued_per_security is not None
                    source = (
                        f"{price_text}% · номинал {self._rub(price.unpaid_face.minor)} · "
                        f"НКД за бумагу {self._rub(price.accrued_per_security.minor)}"
                    )
                unrealized = valued.market_minor - position.cost_minor
                layout.addWidget(
                    _label(
                        f"Оценка {self._rub(valued.market_minor)} · "
                        f"Изменение стоимости {self._rub(unrealized)}"
                    )
                )
                layout.addWidget(_label(f"Ручная цена от {date_text(price.quoted_on)} · {source}"))
        sources = {event.id: event for event in request.events}
        tabs = QTabWidget()
        self.lots = self._section(
            tabs,
            "Открытые партии",
            ["Дата покупки", "Порядок", "Бумаг", "Стоимость, ₽"],
            [
                [
                    date_text(lot.acquired_on),
                    str(sources[lot.acquisition_id].sequence),
                    str(lot.quantity),
                    format_report_amount(lot.cost_minor, RUB),
                ]
                for lot in position.lots
            ],
            (1, 2, 3),
        )
        self.disposals = self._section(
            tabs,
            "Продажи и погашения",
            ["Дата", "Вид", "Бумаг", "Получено, ₽", "Стоимость, ₽", "Результат, ₽"],
            [
                [
                    date_text(row.trade.occurred_on),
                    TRADE_LABELS[row.trade.kind],
                    str(row.trade.quantity),
                    format_report_amount(row.trade.cash_delta_minor, RUB),
                    format_report_amount(row.cost_minor, RUB),
                    format_report_amount(row.realized_minor, RUB),
                ]
                for row in portfolio.disposals
            ],
            (2, 3, 4, 5),
        )
        self.allocations = self._section(
            tabs,
            "Списания FIFO",
            ["Дата продажи", "Дата покупки", "Порядок покупки", "Бумаг", "Стоимость, ₽"],
            [
                [
                    date_text(disposal.trade.occurred_on),
                    date_text(sources[item.acquisition_id].occurred_on),
                    str(sources[item.acquisition_id].sequence),
                    str(item.quantity),
                    format_report_amount(item.cost_minor, RUB),
                ]
                for disposal in portfolio.disposals
                for item in disposal.allocations
            ],
            (2, 3, 4),
        )
        self.distributions = self._section(
            tabs,
            "Выплаты",
            ["Дата", "Вид", "Зачислено, ₽"],
            [
                [
                    date_text(item.occurred_on),
                    DISTRIBUTION_LABELS[item.kind],
                    format_report_amount(item.received.minor, RUB),
                ]
                for item in portfolio.distributions
            ],
            (2,),
        )
        layout.addWidget(tabs, 1)

    @staticmethod
    def _rub(minor: int) -> str:
        return f"{format_report_amount(minor, RUB)} ₽"

    @staticmethod
    def _section(
        tabs: QTabWidget,
        name: str,
        headers: list[str],
        rows: list[list[str]],
        right: tuple[int, ...],
    ) -> QTableView:
        table = _table(name)
        replace_table(table, headers, rows, right_columns=right)
        table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        tabs.addTab(table, name)
        return table
