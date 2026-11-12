"""Ввод сделок, выплат и ручной цены в черновике позиции."""

from decimal import Decimal, InvalidOperation
from uuid import uuid4

from PySide6.QtCore import QDate
from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QFormLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from personal_finance.domain.errors import ValidationError
from personal_finance.domain.investment_prices import ManualPrice, PriceUnit
from personal_finance.domain.investments import (
    DistributionKind,
    PortfolioEvent,
    SecurityDistribution,
    SecurityKind,
    SecurityTrade,
    TradeKind,
)
from personal_finance.domain.models import RUB, Account, Money
from personal_finance.presentation.analytics import _label, format_report_amount
from personal_finance.presentation.recurring_payments import _date, _date_input

SECURITY_LABELS = {
    SecurityKind.STOCK: "Акция",
    SecurityKind.BOND: "Облигация",
    SecurityKind.FUND: "Биржевой фонд",
}
TRADE_LABELS = {
    TradeKind.BUY: "Покупка",
    TradeKind.SELL: "Продажа",
    TradeKind.REDEEM: "Погашение",
}
DISTRIBUTION_LABELS = {DistributionKind.DIVIDEND: "Дивиденды", DistributionKind.COUPON: "Купон"}
MAX_UI_INTEGER = 2_147_483_647


def _money_input(name: str) -> QLineEdit:
    field = QLineEdit()
    field.setAccessibleName(name)
    field.setMaxLength(64)
    return field


class InvestmentEventEditor(QDialog):
    def __init__(
        self,
        parent: QWidget,
        account: Account,
        security_id: str,
        security_kind: SecurityKind,
        day: QDate,
        sequence: int,
        event: PortfolioEvent | None = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("Сделка или выплата")
        self.account = account
        self.security_id = security_id
        self.bond = security_kind is SecurityKind.BOND
        self.event_id = event.id if event else str(uuid4())
        self.entry: PortfolioEvent | None = None
        layout = QVBoxLayout(self)
        form = self.form = QFormLayout()
        layout.addLayout(form)
        self.kind = QComboBox()
        self.kind.setAccessibleName("Вид операции")
        for value, name in TRADE_LABELS.items():
            if value is not TradeKind.REDEEM or self.bond:
                self.kind.addItem(name, value.value)
        payout = DistributionKind.COUPON if self.bond else DistributionKind.DIVIDEND
        self.kind.addItem(DISTRIBUTION_LABELS[payout], payout.value)
        form.addRow("Вид", self.kind)
        self.day = _date_input(QDate(event.occurred_on) if event else day, "Дата операции")
        form.addRow("Дата", self.day)
        self.sequence = QSpinBox()
        self.sequence.setRange(1, MAX_UI_INTEGER)
        self.sequence.setAccessibleName("Порядок операции в этот день")
        self.sequence.setValue(event.sequence if event else sequence)
        form.addRow("Порядок в этот день", self.sequence)
        self.quantity = QSpinBox()
        self.quantity.setRange(1, MAX_UI_INTEGER)
        self.quantity.setAccessibleName("Количество бумаг")
        form.addRow("Количество бумаг", self.quantity)
        self.amount = _money_input("Денежная сумма операции в рублях")
        form.addRow("Сумма, ₽", self.amount)
        self.fee = _money_input("Комиссия в рублях")
        self.fee.setText("0,00")
        form.addRow("Комиссия, ₽", self.fee)
        self.accrued = _money_input("НКД всей сделки в рублях")
        self.accrued.setPlaceholderText("0,00, если НКД отсутствует")
        form.addRow("НКД всей сделки, ₽", self.accrued)
        self.feedback = _label("")
        self.feedback.setObjectName("feedbackError")
        self.feedback.hide()
        layout.addWidget(self.feedback)
        self.apply = QPushButton("Применить")
        self.apply.setObjectName("primary")
        self.apply.clicked.connect(self._apply)
        layout.addWidget(self.apply)
        if event is not None:
            position = self.kind.findData(event.kind.value)
            if position >= 0:
                self.kind.setCurrentIndex(position)
            amount = event.amount if isinstance(event, SecurityTrade) else event.received
            self.amount.setText(format_report_amount(amount.minor, RUB))
            if isinstance(event, SecurityTrade):
                self.quantity.setValue(event.quantity)
                self.fee.setText(format_report_amount(event.fee.minor, RUB))
                if event.accrued_interest is not None:
                    self.accrued.setText(format_report_amount(event.accrued_interest.minor, RUB))
        self.kind.currentIndexChanged.connect(self._show_fields)
        self._show_fields()

    def _show_fields(self) -> None:
        trade = self.kind.currentData() in TRADE_LABELS
        self.form.setRowVisible(self.quantity, trade)
        self.form.setRowVisible(self.fee, trade)
        self.form.setRowVisible(self.accrued, trade and self.bond)
        label = self.form.labelForField(self.amount)
        if isinstance(label, QLabel):
            label.setText("Сумма без НКД и комиссии, ₽" if trade else "Зачислено, ₽")

    def _apply(self) -> None:
        fields = (
            self.event_id,
            self.account.id,
            self.security_id,
            _date(self.day),
            self.sequence.value(),
        )
        try:
            amount = Money.parse(self.amount.text(), RUB)
            kind = self.kind.currentData()
            if kind in TRADE_LABELS:
                self.entry = SecurityTrade(
                    *fields,
                    TradeKind(kind),
                    self.quantity.value(),
                    amount,
                    Money.parse(self.fee.text(), RUB),
                    Money.parse(self.accrued.text(), RUB) if self.bond else None,
                )
            else:
                self.entry = SecurityDistribution(*fields, DistributionKind(kind), amount)
        except ValidationError as error:
            self.feedback.setText(str(error))
            self.feedback.show()
            return
        self.accept()


class ManualPriceEditor(QDialog):
    def __init__(
        self,
        parent: QWidget,
        security_id: str,
        security_kind: SecurityKind,
        day: QDate,
        price: ManualPrice | None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("Ручная цена")
        self.security_id = security_id
        self.bond = security_kind is SecurityKind.BOND
        self.price: ManualPrice | None = None
        layout = QVBoxLayout(self)
        fields = QFormLayout()
        layout.addLayout(fields)
        self.day = _date_input(QDate(price.quoted_on) if price else day, "Дата цены")
        fields.addRow("Дата цены", self.day)
        title = "Цена, % от номинала" if self.bond else "Цена за бумагу, ₽"
        self.value = _money_input(title)
        fields.addRow(title, self.value)
        self.nominal = _money_input("Непогашенный номинал одной бумаги в рублях")
        self.accrued = _money_input("НКД за одну бумагу в рублях")
        if self.bond:
            fields.addRow("Номинал одной бумаги, ₽", self.nominal)
            fields.addRow("НКД за одну бумагу, ₽", self.accrued)
        else:
            self.nominal.hide()
            self.accrued.hide()
        if price is not None:
            self.value.setText(format(price.value, "f").replace(".", ","))
            if price.unpaid_face is not None:
                self.nominal.setText(format_report_amount(price.unpaid_face.minor, RUB))
            if price.accrued_per_security is not None:
                self.accrued.setText(format_report_amount(price.accrued_per_security.minor, RUB))
        self.feedback = _label("")
        self.feedback.setObjectName("feedbackError")
        self.feedback.hide()
        layout.addWidget(self.feedback)
        self.apply = QPushButton("Применить")
        self.apply.setObjectName("primary")
        self.apply.clicked.connect(self._apply)
        layout.addWidget(self.apply)

    def _apply(self) -> None:
        try:
            try:
                number = Decimal(self.value.text().strip().replace(" ", "").replace(",", "."))
            except InvalidOperation as error:
                raise ValidationError("Введите точное числовое значение цены.") from error
            self.price = ManualPrice(
                self.security_id,
                _date(self.day),
                PriceUnit.PERCENT_OF_FACE if self.bond else PriceUnit.CASH_PER_SECURITY,
                number,
                RUB,
                Money.parse(self.nominal.text(), RUB) if self.bond else None,
                Money.parse(self.accrued.text(), RUB) if self.bond else None,
            )
        except ValidationError as error:
            self.feedback.setText(str(error))
            self.feedback.show()
            return
        self.accept()
