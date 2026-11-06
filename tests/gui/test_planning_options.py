"""Загрузка условий в форму без записи и без подмены сохранённого отбора."""

from dataclasses import replace
from datetime import date

import pytest

from personal_finance.domain.analytics import ReportPeriod, ReportScope
from personal_finance.domain.errors import ValidationError
from personal_finance.domain.models import Currency, Money
from personal_finance.domain.planning_scenarios import BudgetPeriodMode, BudgetScenario
from personal_finance.presentation.planning_options import (
    BudgetScenarioOptions,
    SavingsGoalScenarioOptions,
)
from tests.unit.test_planning_scenarios import PERIOD, A, B, goal


@pytest.mark.parametrize(
    "scope",
    [
        ReportScope(),
        ReportScope(frozenset(), frozenset()),
        ReportScope(frozenset((A, B)), frozenset((A,))),
    ],
)
@pytest.mark.parametrize("mode", [BudgetPeriodMode.MONTH, BudgetPeriodMode.DATES])
def test_budget_loaded_scope_and_period_round_trip_without_catalog_fallback(qtbot, scope, mode):
    original = BudgetScenario(PERIOD, Money(100001), scope, mode)
    form = BudgetScenarioOptions([], [], scenario=original)
    qtbot.addWidget(form)
    requests = []
    form.calculationRequested.connect(lambda: requests.append(form.scenario_request))
    form.calculate.click()
    assert requests == [original]
    assert form.result_request == (original.period, original.limit, original.scope)
    assert original.scope == scope


def test_budget_replacing_saved_scope_is_explicit_and_invalid_edit_clears_request(qtbot):
    original = BudgetScenario(PERIOD, Money(10000), ReportScope(frozenset((A, B))))
    form = BudgetScenarioOptions([], [], scenario=original)
    qtbot.addWidget(form)
    form.calculate.click()
    assert form.scenario_request == original
    form.account.setCurrentIndex(0)
    form.calculate.click()
    assert form.scenario_request.scope.account_ids is None
    assert original.scope.account_ids == frozenset((A, B))
    form.limit.setText("-1")
    form.calculate.click()
    assert form.scenario_request is None and form.result_request is None


@pytest.mark.parametrize("deadline", [None, date(2026, 3, 30)])
def test_goal_loading_preserves_anchor_day_sums_and_optional_deadline(qtbot, deadline):
    original = replace(goal(), deadline=deadline)
    form = SavingsGoalScenarioOptions(scenario=original)
    qtbot.addWidget(form)
    requests = []
    form.calculationRequested.connect(lambda: requests.append(form.result_request))
    form.calculate.click()
    assert requests == [original]
    assert form.use_deadline.isChecked() == (deadline is not None)
    assert form.deadline.isEnabled() == (deadline is not None)


def test_loaded_budget_date_range_is_not_replaced_with_current_month(qtbot):
    original = BudgetScenario(
        ReportPeriod(date(2026, 10, 31), date(2026, 11, 1)),
        Money(50000),
    )
    form = BudgetScenarioOptions([], [], scenario=original)
    qtbot.addWidget(form)
    assert form.period_mode.currentIndex() == 1
    form.calculate.click()
    assert form.scenario_request == original


def test_forms_refuse_to_label_non_rub_conditions_as_rubles():
    currency = Currency("USD", 2)
    with pytest.raises(ValidationError, match="RUB"):
        BudgetScenarioOptions([], [], scenario=BudgetScenario(PERIOD, Money(1, currency)))
    other = replace(
        goal(), current=Money(0, currency), target=Money(1, currency), monthly=Money(0, currency)
    )
    with pytest.raises(ValidationError, match="RUB"):
        SavingsGoalScenarioOptions(scenario=other)
