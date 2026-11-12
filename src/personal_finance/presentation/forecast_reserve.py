"""Контроль минимального остатка по выбранному прогнозу."""

from PySide6.QtWidgets import QHBoxLayout, QLineEdit, QPushButton, QVBoxLayout, QWidget

from personal_finance.domain.balance_forecast import BalanceForecast, check_reserve
from personal_finance.domain.errors import ValidationError
from personal_finance.domain.models import Money
from personal_finance.presentation.analytics import _label, format_report_amount
from personal_finance.presentation.deposit_scenarios import date_text


class ForecastReserve(QWidget):
    def __init__(self, forecast: BalanceForecast) -> None:
        super().__init__()
        self.forecast = forecast
        self.reserve: Money | None = None
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(6)
        actions = QHBoxLayout()
        actions.setSpacing(6)
        self.amount = QLineEdit()
        self.amount.setMaxLength(64)
        self.amount.setPlaceholderText(f"Сумма резерва, {forecast.currency.code}")
        self.amount.setAccessibleName("Сумма резерва")
        actions.addWidget(self.amount, 1)
        self.check = QPushButton("Проверить резерв")
        self.check.clicked.connect(self._check)
        self.amount.returnPressed.connect(self._check)
        actions.addWidget(self.check)
        layout.addLayout(actions)
        self.result = _label("")
        self.result.hide()
        layout.addWidget(self.result)
        self.error = _label("")
        self.error.setObjectName("feedbackError")
        self.error.hide()
        layout.addWidget(self.error)
        self.amount.textChanged.connect(self._clear)

    def _clear(self) -> None:
        self.reserve = None
        self.result.hide()
        self.error.hide()

    def _check(self) -> None:
        try:
            value = Money.parse(self.amount.text(), self.forecast.currency)
            if value.minor < 0:
                raise ValidationError("Укажите неотрицательную сумму резерва.")
        except ValidationError as error:
            self._clear()
            self.error.setText(str(error))
            self.error.show()
            return
        self.reserve = value
        self._show_result()

    def update_forecast(self, forecast: BalanceForecast) -> None:
        self.forecast = forecast
        self._show_result()

    def _show_result(self) -> None:
        if self.reserve is None:
            return
        report = check_reserve(self.forecast, self.reserve)
        currency = self.forecast.currency
        minimum = format_report_amount(report.minimum_minor, currency)
        if report.first_below is None:
            status = "Резерв выдержан."
        else:
            shortfall = format_report_amount(report.shortfall_minor, currency)
            status = (
                f"Первый день ниже резерва {date_text(report.first_below)}. "
                f"Наибольшая нехватка {shortfall} {currency.code}."
            )
        self.result.setText(
            f"{status} Минимум {minimum} {currency.code} на {date_text(report.minimum_on)}."
        )
        self.error.hide()
        self.result.show()
