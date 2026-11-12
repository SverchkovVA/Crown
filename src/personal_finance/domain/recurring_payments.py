"""Прогноз ожидаемых поступлений и списаний за выбранный период."""

from collections.abc import Iterator, Sequence
from dataclasses import dataclass
from datetime import date
from enum import StrEnum

from personal_finance.domain.analytics import ReportPeriod
from personal_finance.domain.calendar_dates import add_calendar_months
from personal_finance.domain.errors import ValidationError
from personal_finance.domain.models import Currency, Money, validate_name

MAX_FORECAST_EVENTS = 10000


class PaymentDirection(StrEnum):
    INCOME = "income"
    EXPENSE = "expense"


class RepeatUnit(StrEnum):
    ONCE = "once"
    DAYS = "days"
    WEEKS = "weeks"
    MONTHS = "months"


@dataclass(frozen=True, slots=True)
class PaymentSchedule:
    name: str
    amount: Money
    direction: PaymentDirection
    first_on: date
    unit: RepeatUnit = RepeatUnit.ONCE
    interval: int = 1
    last_on: date | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.name, str):
            raise ValidationError("Укажите название ожидаемого платежа.")
        object.__setattr__(self, "name", validate_name(self.name))
        if not isinstance(self.amount, Money) or self.amount.minor <= 0:
            raise ValidationError("Сумма ожидаемого платежа должна быть больше нуля.")
        if not isinstance(self.direction, PaymentDirection) or not isinstance(
            self.unit, RepeatUnit
        ):
            raise ValidationError("Укажите вид платежа и периодичность.")
        if type(self.first_on) is not date or (
            self.last_on is not None
            and (type(self.last_on) is not date or self.last_on < self.first_on)
        ):
            raise ValidationError("Окончание расписания должно быть не раньше первого платежа.")
        if type(self.interval) is not int or self.interval < 1:
            raise ValidationError("Интервал повтора должен быть положительным целым числом.")
        if self.unit is RepeatUnit.ONCE and (self.interval != 1 or self.last_on is not None):
            raise ValidationError("Для разового платежа укажите только дату первого платежа.")


@dataclass(frozen=True, slots=True)
class ForecastEvent:
    occurs_on: date
    schedule: PaymentSchedule

    @property
    def signed_minor(self) -> int:
        amount = self.schedule.amount.minor
        return amount if self.schedule.direction is PaymentDirection.INCOME else -amount


@dataclass(frozen=True, slots=True)
class PaymentTotals:
    currency: Currency
    income_minor: int = 0
    expense_minor: int = 0

    @property
    def net_minor(self) -> int:
        return self.income_minor - self.expense_minor


@dataclass(frozen=True, slots=True)
class PaymentForecast:
    period: ReportPeriod
    events: tuple[ForecastEvent, ...]
    totals: tuple[PaymentTotals, ...]


def _month_offset(anchor: date, other: date) -> int:
    return (other.year - anchor.year) * 12 + other.month - anchor.month


def _occurrences(schedule: PaymentSchedule, period: ReportPeriod) -> Iterator[date]:
    anchor = schedule.first_on
    start = max(period.start, anchor)
    end = min(period.end, schedule.last_on or date.max)
    if start > end:
        return
    if schedule.unit is RepeatUnit.ONCE:
        if anchor == start:
            yield anchor
        return
    if schedule.unit in (RepeatUnit.DAYS, RepeatUnit.WEEKS):
        days = schedule.interval * (7 if schedule.unit is RepeatUnit.WEEKS else 1)
        elapsed = (start - anchor).days
        first = anchor.toordinal() + (elapsed + days - 1) // days * days
        for ordinal in range(first, end.toordinal() + 1, days):
            yield date.fromordinal(ordinal)
        return
    step = schedule.interval
    first_offset = _month_offset(anchor, start) // step * step
    for offset in range(first_offset, _month_offset(anchor, end) + 1, step):
        due = add_calendar_months(anchor, offset)
        if due is not None and start <= due <= end:
            yield due


def forecast_payments(
    schedules: Sequence[PaymentSchedule], period: ReportPeriod
) -> PaymentForecast:
    if not isinstance(period, ReportPeriod):
        raise ValidationError("Укажите период прогноза платежей.")
    events: list[ForecastEvent] = []
    totals: dict[str, PaymentTotals] = {}
    for schedule in schedules:
        if not isinstance(schedule, PaymentSchedule):
            raise ValidationError("Проверьте условия ожидаемых платежей.")
        amount = schedule.amount
        code = amount.currency.code
        accumulated = totals.get(code, PaymentTotals(amount.currency))
        if amount.currency != accumulated.currency:
            raise ValidationError("Одинаковый код валюты указан с разной точностью.")
        income, expense = accumulated.income_minor, accumulated.expense_minor
        for due in _occurrences(schedule, period):
            if len(events) >= MAX_FORECAST_EVENTS:
                raise ValidationError("В прогнозе больше 10 000 платежей. Сократите период.")
            events.append(ForecastEvent(due, schedule))
            if schedule.direction is PaymentDirection.INCOME:
                income += amount.minor
            else:
                expense += amount.minor
        totals[code] = PaymentTotals(amount.currency, income, expense)
    events.sort(key=lambda item: item.occurs_on)
    return PaymentForecast(
        period,
        tuple(events),
        tuple(
            totals[code]
            for code in sorted(totals)
            if totals[code].income_minor or totals[code].expense_minor
        ),
    )
