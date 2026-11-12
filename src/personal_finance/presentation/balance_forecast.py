"""Результат прогноза остатка в рабочей области отчётов."""

from PySide6.QtWidgets import QDialog, QHeaderView, QTabWidget, QVBoxLayout, QWidget

from personal_finance.domain.balance_forecast import BalanceForecast
from personal_finance.presentation.analytics import _label, format_report_amount
from personal_finance.presentation.deposit_scenarios import date_text
from personal_finance.presentation.forecast_reserve import ForecastReserve
from personal_finance.presentation.payment_matching import PaymentMatching
from personal_finance.presentation.recurring_payments import _table
from personal_finance.presentation.tables import replace_table


class BalanceForecastDialog(QDialog):
    def __init__(self, forecast: BalanceForecast, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.forecast = forecast
        self.setWindowTitle("Прогноз остатка")
        layout = QVBoxLayout(self)
        currency = forecast.currency

        account = forecast.account
        scope = account.name if account else f"Счетов в прогнозе: {len(forecast.accounts)}"
        layout.addWidget(
            _label(
                f"{scope} · {date_text(forecast.period.start)} — "
                f"{date_text(forecast.period.end)} · {currency.code}"
            )
        )
        self.summary = _label("")
        layout.addWidget(self.summary)
        self.reserve = ForecastReserve(forecast)
        layout.addWidget(self.reserve)
        if not forecast.days:
            layout.addWidget(_label("В выбранном периоде движений нет."))
        tabs = QTabWidget(self)
        self.days = _table("Прогноз остатка по дням")
        self.movements = _table("Журнал и ожидаемые платежи")
        tabs.addTab(self.days, "По дням")
        tabs.addTab(self.movements, "Движения")
        self.matching = None
        if account is None:
            accounts = _table("Счета в прогнозе")
            replace_table(accounts, ["Счёт"], [[row.name] for row in forecast.accounts])
            accounts.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
            tabs.addTab(accounts, "Счета")
        if account is not None and forecast.planned and forecast.booked:
            matching = PaymentMatching(forecast)
            if matching.indices:
                self.matching = matching
                matching.changed.connect(self._apply_forecast)
                tabs.addTab(matching, "Сопоставление")
        layout.addWidget(tabs, 1)
        self._apply_forecast(forecast)

    def _apply_forecast(self, forecast: BalanceForecast) -> None:
        self.forecast = forecast
        self.reserve.update_forecast(forecast)
        currency = forecast.currency

        def amount(minor: int) -> str:
            return format_report_amount(minor, currency)

        self.summary.setText(
            f"Остаток на начало {amount(forecast.opening_minor)} · "
            f"Прогноз на конец {amount(forecast.closing_minor)}"
        )
        replace_table(
            self.days,
            ["Дата", "По журналу", "Ожидается", "Остаток к концу дня"],
            [
                [
                    date_text(row.day),
                    amount(row.booked_minor),
                    amount(row.planned_minor),
                    amount(row.closing_minor),
                ]
                for row in forecast.days
            ],
            right_columns=(1, 2, 3),
        )
        self.days.horizontalHeader().setSectionResizeMode(3, QHeaderView.ResizeMode.Stretch)
        linked = {item.expected_index for item in forecast.matches}
        accounts = {account.id: account.name for account in forecast.accounts}
        multiple = len(accounts) > 1
        rows = [
            (
                item.operation.draft.occurred_on,
                "Журнал",
                accounts[item.account_id],
                item.operation.draft.note or "Операция",
                item.minor,
            )
            for item in forecast.booked
        ] + [
            (
                item.occurs_on,
                "Учтён в журнале" if index in linked else "Ожидается",
                "—",
                item.schedule.name,
                None if index in linked else item.signed_minor,
            )
            for index, item in enumerate(forecast.planned)
        ]
        rows.sort(key=lambda row: row[0])
        columns = ["Дата", "Источник"] + (["Счёт"] if multiple else []) + ["Описание", "Движение"]
        replace_table(
            self.movements,
            columns,
            [
                [date_text(day), source]
                + ([account] if multiple else [])
                + [note, "—" if minor is None else amount(minor)]
                for day, source, account, note, minor in rows
            ],
            right_columns=(len(columns) - 1,),
        )
        self.movements.horizontalHeader().setSectionResizeMode(
            len(columns) - 2, QHeaderView.ResizeMode.Stretch
        )
