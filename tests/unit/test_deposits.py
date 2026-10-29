from dataclasses import FrozenInstanceError, replace
from datetime import date, timedelta
from decimal import Decimal, Inexact, localcontext
from fractions import Fraction
from itertools import product
from uuid import UUID

import pytest

import personal_finance.domain.deposits as d
from personal_finance.domain.errors import ValidationError
from personal_finance.domain.models import MAX_MINOR, Currency, Money


def uid(n):
    return str(UUID(int=n))


def scenario(**changes):
    value = d.DepositScenario(
        opened_on=date(2023, 12, 30),
        matures_on=date(2024, 1, 4),
        initial=Money(100001),
        rates=(
            d.DepositRate(uid(1), date(2023, 12, 30), Decimal("7.12345678")),
            d.DepositRate(uid(2), date(2024, 1, 2), Decimal("12.5")),
        ),
        movements=(
            d.DepositMovement(uid(3), date(2023, 12, 31), Money(50003)),
            d.DepositMovement(uid(4), date(2024, 1, 2), Money(-25007)),
            d.DepositMovement(uid(5), date(2024, 1, 2), Money(100)),
        ),
        payout_dates=(date(2024, 1, 1), date(2024, 1, 4)),
        boundary=d.AccrualBoundary.MATURITY_INCLUDED,
        balance_moment=d.BalanceMoment.AFTER_MOVEMENTS,
        day_count=d.DayCount.CALENDAR_YEAR,
        rounding=d.InterestRounding.HALF_UP,
        rounding_stage=d.RoundingStage.PAYOUT,
        capitalize=True,
        allow_topups=True,
        allow_withdrawals=True,
        minimum=Money(0),
    )
    return replace(value, **changes)


def oracle_round(value, mode):
    floor = value.numerator // value.denominator
    delta = value - floor
    if mode == d.InterestRounding.DOWN:
        return floor
    if delta < Fraction(1, 2):
        return floor
    if delta > Fraction(1, 2):
        return floor + 1
    return floor + 1 if mode == d.InterestRounding.HALF_UP else floor + floor % 2


def oracle(value):
    total = value.initial.minor
    waiting = Fraction(0)
    answers = []
    first = value.opened_on + timedelta(days=value.boundary == d.AccrualBoundary.MATURITY_INCLUDED)
    last = value.matures_on - timedelta(days=value.boundary == d.AccrualBoundary.OPENING_INCLUDED)
    day = value.opened_on
    while day <= value.matures_on:
        earlier = total
        total += sum(m.amount.minor for m in value.movements if m.occurred_on == day)
        base = earlier if value.balance_moment == d.BalanceMoment.BEFORE_MOVEMENTS else total
        rate = max((r for r in value.rates if r.effective_on <= day), key=lambda r: r.effective_on)
        leap = day.year % 4 == 0 and (day.year % 100 != 0 or day.year % 400 == 0)
        divisor = 366 if value.day_count == d.DayCount.CALENDAR_YEAR and leap else 365
        exact = (
            Fraction(base) * Fraction(rate.annual_percent) / (100 * divisor)
            if first <= day <= last
            else Fraction(0)
        )
        waiting += (
            oracle_round(exact, value.rounding)
            if value.rounding_stage == d.RoundingStage.DAILY
            else exact
        )
        pay = 0
        if day in value.payout_dates:
            pay = oracle_round(waiting, value.rounding)
            waiting = Fraction(0)
            if value.capitalize:
                total += pay
        answers.append((base, exact, pay, total))
        if day == value.matures_on:
            break
        day += timedelta(days=1)
    return answers


@pytest.mark.parametrize(
    "boundary,moment,count,rounding,stage,capitalize",
    list(
        product(
            d.AccrualBoundary,
            d.BalanceMoment,
            d.DayCount,
            d.InterestRounding,
            d.RoundingStage,
            (False, True),
        )
    ),
)
def test_independent_fraction_oracle(boundary, moment, count, rounding, stage, capitalize):
    value = scenario(
        boundary=boundary,
        balance_moment=moment,
        day_count=count,
        rounding=rounding,
        rounding_stage=stage,
        capitalize=capitalize,
    )
    result = d.forecast_deposit(value)
    assert [
        (
            v.base_minor,
            Fraction(v.interest_numerator, d.INTEREST_DENOMINATOR),
            v.payout_minor,
            v.closing_minor,
        )
        for v in result.days
    ] == oracle(value)
    assert result.interest_minor == sum(v[2] for v in oracle(value))
    assert result.closing_minor == oracle(value)[-1][3]


@pytest.mark.parametrize(
    "mode,expected",
    [
        (d.InterestRounding.HALF_UP, 1),
        (d.InterestRounding.HALF_EVEN, 0),
        (d.InterestRounding.DOWN, 0),
    ],
)
def test_exact_half_after_365_fractional_days(mode, expected):
    start = date(2026, 1, 1)
    end = date(2027, 1, 1)
    value = scenario(
        opened_on=start,
        matures_on=end,
        initial=Money(1),
        rates=(d.DepositRate(uid(1), start, Decimal("50")),),
        movements=(),
        payout_dates=(end,),
        boundary=d.AccrualBoundary.OPENING_INCLUDED,
        day_count=d.DayCount.FIXED_365,
        rounding=mode,
        capitalize=False,
    )
    result = d.forecast_deposit(value)
    assert sum(v.interest_numerator for v in result.days) * 2 == d.INTEREST_DENOMINATOR
    assert result.interest_minor == expected and result.closing_minor == 1
    assert (
        d.forecast_deposit(replace(value, rounding_stage=d.RoundingStage.DAILY)).interest_minor == 0
    )


def test_half_even_odd_whole_and_rounding_context_independence():
    start = date(2026, 1, 1)
    end = date(2027, 1, 1)
    value = scenario(
        opened_on=start,
        matures_on=end,
        initial=Money(3),
        rates=(d.DepositRate(uid(1), start, Decimal("50")),),
        movements=(),
        payout_dates=(end,),
        boundary=d.AccrualBoundary.OPENING_INCLUDED,
        day_count=d.DayCount.FIXED_365,
        rounding=d.InterestRounding.HALF_EVEN,
        capitalize=False,
    )
    with localcontext() as context:
        context.prec = 2
        context.traps[Inexact] = True
        result = d.forecast_deposit(value)
    assert result.interest_minor == 2


def test_capitalization_starts_next_day():
    start = date(2026, 1, 1)
    end = date(2026, 1, 3)
    value = scenario(
        opened_on=start,
        matures_on=end,
        initial=Money(100),
        rates=(d.DepositRate(uid(1), start, Decimal("365")),),
        movements=(),
        payout_dates=(date(2026, 1, 2), end),
        day_count=d.DayCount.FIXED_365,
    )
    result = d.forecast_deposit(value)
    assert [v.base_minor for v in result.days] == [100, 100, 101]
    assert [v.payout_minor for v in result.days] == [0, 1, 1]
    assert result.interest_minor == 2 and result.closing_minor == 102
    separate = d.forecast_deposit(replace(value, capitalize=False))
    assert [v.base_minor for v in separate.days] == [100, 100, 100]
    assert separate.closing_minor == 100


def test_zero_rate_order_and_immutability():
    value = scenario()
    reverse = replace(
        value,
        rates=tuple(reversed(value.rates)),
        movements=tuple(reversed(value.movements)),
        payout_dates=tuple(reversed(value.payout_dates)),
    )
    assert d.forecast_deposit(value) == d.forecast_deposit(reverse)
    zero = replace(value, rates=(d.DepositRate(uid(1), value.opened_on, Decimal(0)),))
    result = d.forecast_deposit(zero)
    assert result.interest_minor == 0 and result.closing_minor == 125097
    with pytest.raises(FrozenInstanceError):
        result.days = ()
    with pytest.raises(FrozenInstanceError):
        result.days[0].base_minor = 0


@pytest.mark.parametrize(
    "annual",
    [
        1.2,
        1,
        True,
        "5",
        Decimal("NaN"),
        Decimal("Infinity"),
        Decimal("-1"),
        Decimal("1000.00000001"),
        Decimal("0.000000001"),
        Decimal("1e-1000000000"),
        Decimal("1e1000000000"),
    ],
)
def test_invalid_rate_rejected_without_decimal_blowup(annual):
    with pytest.raises(ValidationError):
        d.DepositRate(uid(1), date(2026, 1, 1), annual)


@pytest.mark.parametrize(
    "changes",
    [
        {"opened_on": date(2024, 1, 4)},
        {"matures_on": date(2300, 1, 1)},
        {"initial": Money(0)},
        {"minimum": Money(-1)},
        {"minimum": Money(100002)},
        {"minimum": Money(0, Currency("USD", 2))},
        {"boundary": "opening_included"},
        {"capitalize": 1},
        {"rates": ()},
        {"rates": []},
        {"movements": []},
        {"payout_dates": ()},
        {"payout_dates": (date(2024, 1, 3),)},
        {"payout_dates": (date(2023, 12, 29), date(2024, 1, 4))},
        {"payout_dates": (date(2024, 1, 4), date(2024, 1, 4))},
        {"allow_topups": False},
        {"allow_withdrawals": False},
    ],
)
def test_invalid_scenario(changes):
    with pytest.raises(ValidationError):
        scenario(**changes)


def test_duplicates_wrong_dates_currency_balance_and_overflow():
    value = scenario()
    bad = [
        {"rates": (value.rates[0], value.rates[0])},
        {"rates": (replace(value.rates[0], effective_on=date(2023, 12, 31)),)},
        {
            "rates": (
                value.rates[0],
                replace(value.rates[1], revision_id=value.rates[0].revision_id),
            )
        },
        {"movements": (value.movements[0], value.movements[0])},
        {"movements": (replace(value.movements[0], occurred_on=date(2023, 1, 1)),)},
        {"movements": (replace(value.movements[0], amount=Money(1, Currency("USD", 2))),)},
        {"movements": (replace(value.movements[0], amount=Money(-100002)),)},
        {
            "initial": Money(MAX_MINOR),
            "movements": (replace(value.movements[0], amount=Money(1)),),
            "capitalize": False,
        },
        {"initial": Money(MAX_MINOR), "movements": ()},
    ]
    for changes in bad:
        with pytest.raises(ValidationError):
            d.forecast_deposit(replace(value, **changes))


@pytest.mark.parametrize("start,end", [(date.min, date(1, 1, 2)), (date(9999, 12, 30), date.max)])
def test_calendar_extremes(start, end):
    value = scenario(
        opened_on=start,
        matures_on=end,
        rates=(d.DepositRate(uid(1), start, Decimal(0)),),
        movements=(),
        payout_dates=(end,),
    )
    assert [v.day for v in d.forecast_deposit(value).days] == [start, end]


@pytest.mark.parametrize(
    "base,mode,expected",
    [
        (50, d.InterestRounding.HALF_UP, 1),
        (50, d.InterestRounding.HALF_EVEN, 0),
        (50, d.InterestRounding.DOWN, 0),
        (150, d.InterestRounding.HALF_UP, 2),
        (150, d.InterestRounding.HALF_EVEN, 2),
        (150, d.InterestRounding.DOWN, 1),
    ],
)
def test_exact_daily_rounding_halves(base, mode, expected):
    start = date(2026, 1, 1)
    end = date(2026, 1, 2)
    value = scenario(
        opened_on=start,
        matures_on=end,
        initial=Money(base),
        rates=(d.DepositRate(uid(1), start, Decimal(365)),),
        movements=(),
        payout_dates=(end,),
        day_count=d.DayCount.FIXED_365,
        rounding=mode,
        rounding_stage=d.RoundingStage.DAILY,
        capitalize=False,
    )
    assert d.forecast_deposit(value).interest_minor == expected


@pytest.mark.parametrize("currency", [Currency("JPY", 0), Currency("ABC", 8)])
def test_minor_unit_calculation_independent_of_currency_exponent(currency):
    start = date(2026, 1, 1)
    end = date(2026, 1, 2)
    value = scenario(
        opened_on=start,
        matures_on=end,
        initial=Money(50, currency),
        minimum=Money(0, currency),
        rates=(d.DepositRate(uid(1), start, Decimal(365)),),
        movements=(),
        payout_dates=(end,),
        day_count=d.DayCount.FIXED_365,
        capitalize=False,
    )
    assert d.forecast_deposit(value).interest_minor == 1


def test_smallest_supported_rate_and_safe_extreme_zero():
    start = date(2026, 1, 1)
    end = date(2026, 1, 2)
    value = scenario(
        opened_on=start,
        matures_on=end,
        initial=Money(MAX_MINOR),
        rates=(d.DepositRate(uid(1), start, Decimal("0.00000001")),),
        movements=(),
        payout_dates=(end,),
        day_count=d.DayCount.FIXED_365,
        capitalize=False,
    )
    result = d.forecast_deposit(value)
    assert result.interest_minor == oracle(value)[-1][2]
    zero = replace(value, rates=(d.DepositRate(uid(1), start, Decimal("0e-1000000000")),))
    assert d.forecast_deposit(zero).interest_minor == 0
