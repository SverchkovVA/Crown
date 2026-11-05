from calendar import monthcalendar
from datetime import date, timedelta

import pytest
from hypothesis import given
from hypothesis import strategies as st

from personal_finance.domain.errors import ValidationError
from personal_finance.domain.models import MAX_MINOR, Currency, Money
from personal_finance.domain.savings_goals import SavingsGoalScenario, project_savings_goal


def test_goal_dates_use_original_day_without_drifting_after_short_month():
    scenario = SavingsGoalScenario(
        date(2026, 1, 31), Money(100), Money(300), Money(100), date(2026, 3, 30)
    )
    result = project_savings_goal(scenario)
    assert result.remaining_minor == 200 and result.progress_basis_points == 3333
    assert result.first_contribution_on == date(2026, 2, 28)
    assert result.contributions_to_goal == 2 and result.reached_on == date(2026, 3, 31)
    assert result.contributions_by_deadline == 1
    assert result.required_monthly_minor == 200 and result.at_deadline_minor == 200


def test_goal_counts_deadline_payment_and_rounds_required_minor_units_up():
    result = project_savings_goal(
        SavingsGoalScenario(date(2024, 1, 31), Money(0), Money(201), Money(101), date(2024, 3, 31))
    )
    assert result.first_contribution_on == date(2024, 2, 29)
    assert (
        result.contributions_by_deadline,
        result.required_monthly_minor,
        result.at_deadline_minor,
    ) == (2, 101, 202)
    assert result.contributions_to_goal == 2 and result.reached_on == date(2024, 3, 31)


def test_goal_already_achieved_and_zero_contribution_have_explicit_results():
    result = project_savings_goal(
        SavingsGoalScenario(date(2026, 1, 31), Money(400), Money(300), Money(0), date(2026, 1, 31))
    )
    assert result.remaining_minor == 0 and result.progress_basis_points == 10000
    assert result.contributions_to_goal == 0 and result.reached_on == date(2026, 1, 31)
    assert result.required_monthly_minor == 0 and result.at_deadline_minor == 400
    unpaid = project_savings_goal(
        SavingsGoalScenario(date(2026, 1, 31), Money(0), Money(300), Money(0), date(2026, 2, 27))
    )
    assert unpaid.reached_on is None and unpaid.contributions_to_goal is None
    assert unpaid.contributions_by_deadline == 0 and unpaid.required_monthly_minor is None


def test_goal_handles_supported_dates_and_very_distant_target_without_iteration():
    result = project_savings_goal(SavingsGoalScenario(date(1, 1, 31), Money(0), Money(1), Money(1)))
    assert result.reached_on == date(1, 2, 28)
    far = project_savings_goal(
        SavingsGoalScenario(date(9999, 12, 31), Money(0), Money(MAX_MINOR), Money(1))
    )
    assert (
        far.contributions_to_goal == MAX_MINOR
        and far.reached_on is None
        and far.first_contribution_on is None
    )
    assert far.contributions_by_deadline is None and far.at_deadline_minor is None
    large = project_savings_goal(
        SavingsGoalScenario(
            date(2026, 1, 1), Money(MAX_MINOR), Money(MAX_MINOR), Money(MAX_MINOR), date(2026, 3, 1)
        )
    )
    assert large.at_deadline_minor == 3 * MAX_MINOR


@pytest.mark.parametrize(
    ("current", "target", "monthly"),
    [
        (Money(-1), Money(10), Money(1)),
        (Money(0), Money(0), Money(1)),
        (Money(0), Money(10), Money(-1)),
        (Money(0, Currency("USD", 2)), Money(10), Money(1)),
    ],
)
def test_goal_rejects_invalid_money_and_mixed_currencies(current, target, monthly):
    with pytest.raises(ValidationError):
        SavingsGoalScenario(date(2026, 1, 1), current, target, monthly)


def test_goal_rejects_deadline_before_start():
    with pytest.raises(ValidationError, match="срок не раньше"):
        SavingsGoalScenario(date(2026, 1, 31), Money(0), Money(10), Money(1), date(2026, 1, 30))


@given(
    st.dates(min_value=date(2000, 1, 1), max_value=date(2030, 12, 31)),
    st.integers(min_value=0, max_value=1100),
    st.integers(min_value=1, max_value=1_000_000),
)
def test_goal_payment_count_and_minimum_contribution_match_calendar(anchor, days, remaining):
    deadline = anchor + timedelta(days=days)
    result = project_savings_goal(
        SavingsGoalScenario(anchor, Money(0), Money(remaining), Money(1), deadline)
    )
    year, month = anchor.year, anchor.month
    paydays = []
    while True:
        if month == 12:
            year, month = year + 1, 1
        else:
            month += 1
        last_day = max(max(week) for week in monthcalendar(year, month))
        payday = date(year, month, min(anchor.day, last_day))
        if payday > deadline:
            break
        paydays.append(payday)
    assert result.contributions_by_deadline == len(paydays)
    required = result.required_monthly_minor
    if paydays:
        assert required is not None
        assert required * len(paydays) >= remaining
        assert (required - 1) * len(paydays) < remaining
    else:
        assert required is None
