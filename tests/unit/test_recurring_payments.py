from dataclasses import replace
from datetime import date, datetime
from decimal import Decimal

import pytest

from personal_finance.domain.analytics import ReportPeriod
from personal_finance.domain.errors import ValidationError
from personal_finance.domain.models import MAX_MINOR, Currency, Money
from personal_finance.domain.recurring_payments import (
    MAX_FORECAST_EVENTS,
    PaymentDirection,
    PaymentSchedule,
    RepeatUnit,
    forecast_payments,
)

FIRST = date(2024, 1, 31)
BASE = PaymentSchedule("Подписка", Money(101), PaymentDirection.EXPENSE, FIRST, RepeatUnit.MONTHS)


def test_months_stay_anchored_and_include_end_date():
    result = forecast_payments(
        [replace(BASE, last_on=date(2024, 4, 30))],
        ReportPeriod(date(2024, 2, 1), date(2024, 5, 31)),
    )
    assert [event.occurs_on for event in result.events] == [
        date(2024, 2, 29),
        date(2024, 3, 31),
        date(2024, 4, 30),
    ]
    assert result.totals[0].expense_minor == 303
    assert result.totals[0].net_minor == -303


@pytest.mark.parametrize(
    ("unit", "interval", "end", "expected"),
    [
        (RepeatUnit.DAYS, 3, date(2024, 2, 8), [date(2024, 2, 3), date(2024, 2, 6)]),
        (RepeatUnit.WEEKS, 2, date(2024, 2, 28), [date(2024, 2, 14), date(2024, 2, 28)]),
        (RepeatUnit.MONTHS, 3, date(2024, 8, 1), [date(2024, 4, 30), date(2024, 7, 31)]),
    ],
)
def test_repeat_starts_on_original_cycle_when_period_starts_later(unit, interval, end, expected):
    result = forecast_payments(
        [replace(BASE, unit=unit, interval=interval)], ReportPeriod(date(2024, 2, 2), end)
    )
    assert [event.occurs_on for event in result.events] == expected


def test_one_time_events_and_currency_totals_are_separate_and_exact():
    yen = Currency("JPY", 0)
    payments = [
        replace(
            BASE,
            name="Ожидаемое поступление",
            amount=Money(MAX_MINOR),
            direction=PaymentDirection.INCOME,
            unit=RepeatUnit.ONCE,
        ),
        replace(BASE, amount=Money(333)),
        replace(BASE, name="Оплата в JPY", amount=Money(500, yen), unit=RepeatUnit.ONCE),
        replace(
            BASE,
            name="Второе поступление",
            amount=Money(MAX_MINOR),
            direction=PaymentDirection.INCOME,
            unit=RepeatUnit.ONCE,
        ),
        replace(BASE, first_on=date(2024, 2, 1)),
    ]
    result = forecast_payments(payments, ReportPeriod(FIRST, FIRST))
    assert [event.schedule for event in result.events] == payments[:4]
    foreign, rubles = result.totals
    assert foreign.currency == yen and foreign.net_minor == -500
    assert rubles.income_minor == 2 * MAX_MINOR and rubles.expense_minor == 333
    assert rubles.net_minor == 2 * MAX_MINOR - 333


def test_empty_period_and_last_supported_date():
    result = forecast_payments([BASE], ReportPeriod(date.min, date.min))
    assert result.events == () and result.totals == ()
    result = forecast_payments(
        [replace(BASE, first_on=date.min, unit=RepeatUnit.DAYS)],
        ReportPeriod(date.max, date.max),
    )
    assert [event.occurs_on for event in result.events] == [date.max]
    result = forecast_payments(
        [replace(BASE, first_on=date(9999, 10, 31))],
        ReportPeriod(date(9999, 11, 1), date.max),
    )
    assert [event.occurs_on for event in result.events] == [date(9999, 11, 30), date.max]


@pytest.mark.parametrize(
    "overrides",
    [
        {"name": " "},
        {"name": 3},
        {"amount": Money(0)},
        {"amount": Money(-1)},
        {"amount": Decimal("10.25")},
        {"first_on": datetime(2024, 1, 31)},
        {"last_on": date(2024, 1, 30)},
        {"interval": True},
        {"interval": 0},
        {"interval": 1.5},
        {"unit": "months"},
        {"direction": "expense"},
        {"unit": RepeatUnit.ONCE, "interval": 2},
        {"unit": RepeatUnit.ONCE, "last_on": FIRST},
    ],
)
def test_invalid_conditions_rejected(overrides):
    with pytest.raises(ValidationError):
        replace(BASE, **overrides)


def test_conflicting_currency_precision_rejected_in_any_order():
    rule = replace(BASE, first_on=date.max, amount=Money(101, Currency("RUB", 3)))
    for schedules in ([BASE, rule], [rule, BASE]):
        with pytest.raises(ValidationError, match="точностью"):
            forecast_payments(schedules, ReportPeriod(FIRST, FIRST))


def test_forecast_bound_preserves_the_last_allowed_row():
    schedule = replace(BASE, unit=RepeatUnit.DAYS, first_on=date.min)
    stop = date.fromordinal(MAX_FORECAST_EVENTS)
    result = forecast_payments([schedule], ReportPeriod(date.min, stop))
    assert len(result.events) == MAX_FORECAST_EVENTS
    assert result.events[-1].occurs_on == stop
    with pytest.raises(ValidationError, match="10 000"):
        forecast_payments([schedule], ReportPeriod(date.min, date.max))
