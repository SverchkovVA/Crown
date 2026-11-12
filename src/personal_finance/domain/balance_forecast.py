"""Прогноз средств на одном счёте из журнала и ожидаемых платежей."""

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date

from personal_finance.domain.analytics import ReportPeriod, ReportScope
from personal_finance.domain.analytics_cashflow import CashMovement, cash_flow_report
from personal_finance.domain.errors import ValidationError
from personal_finance.domain.ledger import Operation, require_identifier
from personal_finance.domain.models import RUB, Account, Currency
from personal_finance.domain.recurring_payments import (
    ForecastEvent,
    PaymentSchedule,
    forecast_payments,
)


@dataclass(frozen=True, slots=True)
class ForecastDay:
    day: date
    booked_minor: int
    planned_minor: int
    closing_minor: int


@dataclass(frozen=True, slots=True)
class BalanceForecast:
    account: Account
    currency: Currency
    period: ReportPeriod
    opening_minor: int
    days: tuple[ForecastDay, ...]
    booked: tuple[CashMovement, ...]
    planned: tuple[ForecastEvent, ...]

    @property
    def closing_minor(self) -> int:
        return self.days[-1].closing_minor if self.days else self.opening_minor


def forecast_balance(
    operations: Sequence[Operation],
    account: Account,
    period: ReportPeriod,
    schedules: Sequence[PaymentSchedule],
    *,
    currency: Currency = RUB,
) -> BalanceForecast:
    if (
        not isinstance(account, Account)
        or not isinstance(currency, Currency)
        or account.currency != currency.code
    ):
        raise ValidationError("Укажите счёт и его валюту для прогноза остатка.")
    require_identifier(account.id)
    if any(
        not isinstance(schedule, PaymentSchedule) or schedule.amount.currency != currency
        for schedule in schedules
    ):
        raise ValidationError("Валюта каждого ожидаемого платежа должна совпадать с валютой счёта.")
    calendar = forecast_payments(schedules, period)
    ledger = cash_flow_report(operations, period, ReportScope(account_ids=frozenset({account.id})))
    if ledger.accounts and ledger.accounts[0].totals.currency != currency:
        raise ValidationError("Точность валюты счёта отличается от проводок журнала.")
    opening = ledger.accounts[0].totals.opening_minor if ledger.accounts else 0
    booked = tuple(
        movement
        for movement in ledger.movements
        if movement.operation.draft.occurred_on >= period.start
    )
    changes: dict[date, list[int]] = {}
    for movement in booked:
        day = movement.operation.draft.occurred_on
        changes.setdefault(day, [0, 0])[0] += movement.minor
    for event in calendar.events:
        changes.setdefault(event.occurs_on, [0, 0])[1] += event.signed_minor
    balance = opening
    days = []
    for day, (booked_minor, planned_minor) in sorted(changes.items()):
        balance += booked_minor + planned_minor
        days.append(ForecastDay(day, booked_minor, planned_minor, balance))
    return BalanceForecast(account, currency, period, opening, tuple(days), booked, calendar.events)
