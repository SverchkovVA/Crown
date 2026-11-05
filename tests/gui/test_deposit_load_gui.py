from dataclasses import replace
from datetime import date
from decimal import Decimal
from itertools import product

import pytest
from PySide6.QtCore import QDate

from personal_finance.domain import deposits as d
from personal_finance.domain.errors import ValidationError
from personal_finance.domain.models import MAX_MINOR, Currency, Money
from personal_finance.presentation.deposits import DepositScenarioDialog
from tests.unit.test_deposits import scenario, uid


@pytest.mark.parametrize(
    "boundary,moment,count,rounding,stage,capital",
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
def test_every_policy_round_trips_without_guessing_dates(
    qtbot, boundary, moment, count, rounding, stage, capital
):
    value = scenario(
        boundary=boundary,
        balance_moment=moment,
        day_count=count,
        rounding=rounding,
        rounding_stage=stage,
        capitalize=capital,
    )
    form = DepositScenarioDialog()
    qtbot.addWidget(form)
    form.load_scenario(value)
    assert form.frequency.currentData() == "saved"
    form._accept()
    assert form.result_scenario == value
    assert d.forecast_deposit(form.result_scenario) == d.forecast_deposit(value)


def test_date_change_requires_explicit_new_graph_and_keeps_inputs(qtbot):
    value = scenario()
    form = DepositScenarioDialog()
    qtbot.addWidget(form)
    form.load_scenario(value)
    form.end.setDate(QDate(2024, 1, 5))
    form._accept()
    assert form.result_scenario is None and "новый график" in form.feedback.text()
    assert form.rate_table.rowCount() == 1 and form.movement_table.rowCount() == 3
    assert form._saved_payout_dates == value.payout_dates
    form.frequency.setCurrentIndex(form.frequency.findData("monthly"))
    form._accept()
    assert form.result_scenario.payout_dates == (date(2024, 1, 5),)
    assert (
        form.result_scenario.rates == value.rates
        and form.result_scenario.movements == value.movements
    )


def test_second_load_clears_old_rows_and_old_result(qtbot):
    form = DepositScenarioDialog()
    qtbot.addWidget(form)
    first = scenario()
    form.load_scenario(first)
    form._accept()
    second = replace(
        first,
        rates=(replace(first.rates[0], revision_id=uid(77)),),
        movements=(),
        payout_dates=(first.matures_on,),
    )
    form.load_scenario(second)
    assert form.result_scenario is None
    assert form.rate_table.rowCount() == 0 and form.movement_table.rowCount() == 0
    assert form.frequency.count() == 3
    form._accept()
    assert form.result_scenario == second


def test_unknown_graph_fails_closed_then_accepts_saved_dates(qtbot):
    value = scenario()
    form = DepositScenarioDialog()
    qtbot.addWidget(form)
    form.load_scenario(value)
    form.frequency.setCurrentIndex(-1)
    form._accept()
    assert form.result_scenario is None and "график" in form.feedback.text()
    form.frequency.setCurrentIndex(form.frequency.findData("saved"))
    form._accept()
    assert form.result_scenario == value


def test_foreign_currency_does_not_mutate_existing_form(qtbot):
    form = DepositScenarioDialog()
    qtbot.addWidget(form)
    value = scenario()
    form.load_scenario(value)
    before = form.initial.text()
    currency = Currency("USD", 2)
    foreign = replace(
        value,
        initial=Money(value.initial.minor, currency),
        minimum=Money(0, currency),
        movements=tuple(
            replace(m, amount=Money(m.amount.minor, currency)) for m in value.movements
        ),
    )
    with pytest.raises(ValidationError, match="RUB"):
        form.load_scenario(foreign)
    assert form.initial.text() == before and form._saved_payout_dates == value.payout_dates
    form._accept()
    assert form.result_scenario == value


def test_large_edit_limit_preserves_saved_conditions_and_allows_forecast(qtbot):
    value = scenario()
    movements = tuple(
        d.DepositMovement(uid(n + 100), date(2024, 1, 2), Money(1)) for n in range(1001)
    )
    huge = replace(value, movements=movements)
    form = DepositScenarioDialog()
    qtbot.addWidget(form)
    form.load_scenario(value)
    with pytest.raises(ValidationError, match="много"):
        form.load_scenario(huge)
    form._accept()
    assert form.result_scenario == value
    assert len(d.forecast_deposit(huge).days) == 6


@pytest.mark.parametrize("year", [100, 1752])
def test_max_money_zero_rate_and_supported_minimum_load_exactly(qtbot, year):
    value = scenario(
        opened_on=date(year, 1, 1),
        matures_on=date(year, 1, 2),
        initial=Money(MAX_MINOR),
        rates=(d.DepositRate(uid(1), date(year, 1, 1), Decimal("0E-999999999")),),
        movements=(),
        payout_dates=(date(year, 1, 2),),
    )
    form = DepositScenarioDialog()
    qtbot.addWidget(form)
    form.load_scenario(value)
    assert form.rate.text() == "0" and form.start.date().year() == year
    form._accept()
    assert form.result_scenario == value


@pytest.mark.parametrize("year", [1, 99])
def test_unsupported_historical_date_never_silently_changes_conditions(qtbot, year):
    old = scenario()
    form = DepositScenarioDialog()
    qtbot.addWidget(form)
    form.load_scenario(old)
    historical = scenario(
        opened_on=date(year, 1, 1),
        matures_on=date(year, 1, 2),
        rates=(d.DepositRate(uid(1), date(year, 1, 1), Decimal(0)),),
        movements=(),
        payout_dates=(date(year, 1, 2),),
    )
    with pytest.raises(ValidationError, match="0100"):
        form.load_scenario(historical)
    form._accept()
    assert form.result_scenario == old
    assert d.forecast_deposit(historical).scenario == historical
