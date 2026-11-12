"""Прогноз средств на одном счёте из журнала и ожидаемых платежей."""

from collections.abc import Sequence
from dataclasses import dataclass, replace
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
class PaymentMatch:
    expected_index: int
    revision_id: str


@dataclass(frozen=True, slots=True)
class BalanceForecast:
    account: Account
    currency: Currency
    period: ReportPeriod
    opening_minor: int
    days: tuple[ForecastDay, ...]
    booked: tuple[CashMovement, ...]
    planned: tuple[ForecastEvent, ...]
    matches: tuple[PaymentMatch, ...] = ()

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
    days = _project_days(opening, booked, calendar.events, set())
    return BalanceForecast(account, currency, period, opening, days, booked, calendar.events)


def _project_days(
    opening: int,
    booked: tuple[CashMovement, ...],
    planned: tuple[ForecastEvent, ...],
    matched_indices: set[int],
) -> tuple[ForecastDay, ...]:
    changes: dict[date, list[int]] = {}
    for movement in booked:
        day = movement.operation.draft.occurred_on
        changes.setdefault(day, [0, 0])[0] += movement.minor
    for index, event in enumerate(planned):
        if index in matched_indices:
            continue
        changes.setdefault(event.occurs_on, [0, 0])[1] += event.signed_minor
    balance = opening
    days = []
    for day, (booked_minor, planned_minor) in sorted(changes.items()):
        balance += booked_minor + planned_minor
        days.append(ForecastDay(day, booked_minor, planned_minor, balance))
    return tuple(days)


def match_payments(forecast: BalanceForecast, matches: Sequence[PaymentMatch]) -> BalanceForecast:
    booked = {movement.operation.revision_id: movement for movement in forecast.booked}
    used_expected: set[int] = set()
    used_revisions: set[str] = set()
    for match in matches:
        if (
            not isinstance(match, PaymentMatch)
            or type(match.expected_index) is not int
            or not 0 <= match.expected_index < len(forecast.planned)
        ):
            raise ValidationError("Выберите ожидаемый платёж в этом прогнозе.")
        require_identifier(match.revision_id)
        if match.expected_index in used_expected or match.revision_id in used_revisions:
            raise ValidationError("Каждый платёж и запись журнала можно сопоставить один раз.")
        expected = forecast.planned[match.expected_index]
        actual = booked.get(match.revision_id)
        if (
            actual is None
            or actual.account_id != forecast.account.id
            or actual.currency != expected.schedule.amount.currency
            or actual.operation.draft.occurred_on != expected.occurs_on
            or actual.minor != expected.signed_minor
        ):
            raise ValidationError("Выберите запись этого счёта с той же датой и суммой.")
        used_expected.add(match.expected_index)
        used_revisions.add(match.revision_id)
    days = _project_days(forecast.opening_minor, forecast.booked, forecast.planned, used_expected)
    return replace(
        forecast, days=days, matches=tuple(sorted(matches, key=lambda v: v.expected_index))
    )
