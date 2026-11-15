from dataclasses import replace
from datetime import date, datetime, timedelta
from decimal import ROUND_DOWN, Decimal, localcontext

import pytest

from personal_finance.domain.deposit_yield import effective_deposit_yield, forecast_deposit_yield
from personal_finance.domain.deposits import forecast_deposit
from personal_finance.domain.errors import ValidationError
from personal_finance.domain.models import MAX_MINOR, Currency, Money
from tests.unit.test_deposit_plan_fact import OPENING, comparison_scenario

START = date(2024, 1, 1)


def flows(*items):
    return tuple((START + timedelta(days=day), Money(value)) for day, value in items)


@pytest.mark.parametrize("closing,expected", [(11000, "10"), (9000, "-10"), (10000, "0")])
def test_two_booked_flows_use_365_actual_days_even_in_leap_year(closing, expected):
    result = effective_deposit_yield(flows((0, -10000), (365, closing)))
    assert abs(result - Decimal(expected)) < Decimal("1e-48")


def test_contributions_and_paid_interest_establish_known_root():
    original = flows((0, -10000), (365, -1000), (365 * 2, 1000), (365 * 3, 13310))
    # Платёж первого года покрывается выплатой второго года и частью расчёта
    # на конец третьего. Проверяем уравнение отдельно от порядка входа.
    result = effective_deposit_yield(original)
    growth = 1 + result / 100
    discounted = (
        Decimal(-10000)
        - Decimal(1000) / growth
        + Decimal(1000) / growth**2
        + Decimal(13310) / growth**3
    )
    assert abs(discounted) < Decimal("1e-22")
    assert effective_deposit_yield(tuple(reversed(original))) == result


def test_merging_same_day_and_rounding_context_are_independent():
    plain = flows((0, -10000), (365, 11000))
    merged = flows((365, 9000), (0, -2000), (365, 2000), (0, -8000), (5, 0))
    reference = effective_deposit_yield(plain)
    with localcontext() as context:
        context.prec = 6
        context.Emax = 9
        context.rounding = ROUND_DOWN
        assert effective_deposit_yield(merged) == reference


def test_tiny_exact_change_remains_positive_and_does_not_round_to_zero():
    result = effective_deposit_yield(flows((0, 1 - MAX_MINOR), (365, MAX_MINOR)))
    assert 0 < result < Decimal("1e-15")


def test_one_day_observation_annualises_without_float_or_overflow():
    result = effective_deposit_yield(flows((0, -100), (1, 101)))
    with localcontext() as context:
        context.prec = 65
        expected = ((Decimal(101) / 100) ** 365 - 1) * 100
        assert abs(result - expected) < Decimal("1e-49")


@pytest.mark.parametrize(
    "sequence, message",
    [
        (flows((0, -1000), (365, 500), (400, -1000), (800, 1800)), "неоднозначна"),
        (flows((0, -1000), (0, 1200)), "более позднюю"),
        (flows((0, 1000), (365, -1000)), "более позднюю"),
        (flows((0, -1000), (36601, 1200)), "срока|Срок"),
        (flows((0, -1000)), "нужны"),
        (
            ((START, Money(-1000)), (START + timedelta(days=1), Money(1000, Currency("USD", 2)))),
            "одной валюте",
        ),
        (
            ((datetime(2024, 1, 1), Money(-1000)), (START + timedelta(days=1), Money(1000))),
            "точную сумму",
        ),
    ],
)
def test_malformed_or_non_unique_series_never_has_guessed_annual_yield(sequence, message):
    with pytest.raises(ValidationError, match=message):
        effective_deposit_yield(sequence)


def test_matches_published_xirr_example():
    values = (
        (date(2008, 1, 1), -10000),
        (date(2008, 3, 1), 2750),
        (date(2008, 10, 30), 4250),
        (date(2009, 2, 15), 3250),
        (date(2009, 4, 1), 2750),
    )
    result = effective_deposit_yield(tuple((day, Money(value)) for day, value in values))
    assert abs(result - Decimal("37.3362535")) < Decimal("0.000001")
    with localcontext() as context:
        context.prec = 64
        growth = 1 + result / 100
        residual = sum(
            Decimal(value) / growth ** (Decimal((day - values[0][0]).days) / 365)
            for day, value in values
        )
        assert abs(residual) < Decimal("1e-47")


@pytest.mark.parametrize("capitalize", [False, True])
def test_scenario_uses_terminal_balance_and_counts_paid_interest_once(capitalize):
    end = OPENING + timedelta(days=365)
    scenario = replace(
        comparison_scenario(), matures_on=end, payout_dates=(end,), capitalize=capitalize
    )
    forecast = forecast_deposit(scenario)
    expected = Decimal("36.5")
    assert abs(forecast_deposit_yield(forecast, end) - expected) < Decimal("1e-48")
    assert forecast_deposit_yield(forecast, end + timedelta(days=30)) == forecast_deposit_yield(
        forecast, end
    )
    assert forecast_deposit_yield(forecast, end - timedelta(days=30)) == Decimal(0)
