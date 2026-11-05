import json
from copy import deepcopy
from datetime import date

import pytest

from personal_finance.domain.analytics import ReportPeriod, ReportScope
from personal_finance.domain.errors import ValidationError
from personal_finance.domain.models import MAX_MINOR, Currency, Money
from personal_finance.domain.planning_scenarios import (
    BudgetPeriodMode,
    BudgetScenario,
    PlanningKind,
    planning_from_json,
    planning_from_payload,
    planning_json,
    planning_kind,
    planning_payload,
)
from personal_finance.domain.savings_goals import SavingsGoalScenario

A = "00000000-0000-4000-8000-000000000001"
B = "00000000-0000-4000-8000-000000000002"
PERIOD = ReportPeriod(date(2024, 2, 1), date(2024, 2, 29))


def budget():
    return BudgetScenario(PERIOD, Money(150000), ReportScope(frozenset((A, B)), frozenset()))


def goal():
    return SavingsGoalScenario(
        date(2026, 1, 31), Money(10000), Money(30000), Money(10000), date(2026, 3, 30)
    )


@pytest.mark.parametrize(
    "scenario",
    [
        budget(),
        goal(),
        BudgetScenario(PERIOD, Money(0), period_mode=BudgetPeriodMode.MONTH),
        BudgetScenario(ReportPeriod(date.min, date.max), Money(MAX_MINOR)),
        SavingsGoalScenario(date.max, Money(MAX_MINOR), Money(MAX_MINOR), Money(0)),
        SavingsGoalScenario(
            date.min,
            Money(0, Currency("JPY", 0)),
            Money(1, Currency("JPY", 0)),
            Money(1, Currency("JPY", 0)),
        ),
    ],
)
def test_planning_round_trip_preserves_exact_conditions(scenario):
    encoded = planning_json(scenario)
    assert planning_from_json(encoded) == scenario
    assert planning_from_payload(planning_payload(scenario)) == scenario
    assert planning_json(planning_from_json(encoded)) == encoded


def test_empty_scope_and_all_scope_have_different_canonical_payloads():
    all_accounts = BudgetScenario(PERIOD, Money(0))
    none = BudgetScenario(PERIOD, Money(0), ReportScope(frozenset()))
    assert planning_payload(all_accounts)["account_ids"] is None
    assert planning_payload(none)["account_ids"] == []
    assert planning_json(all_accounts) != planning_json(none)
    assert planning_kind(all_accounts) == PlanningKind.BUDGET
    assert planning_kind(goal()) == PlanningKind.SAVINGS_GOAL


@pytest.mark.parametrize(
    "start,end",
    [
        (date(2024, 2, 2), date(2024, 2, 29)),
        (date(2024, 2, 1), date(2024, 2, 28)),
        (date(2024, 2, 1), date(2024, 3, 1)),
    ],
)
def test_month_mode_requires_exact_calendar_month(start, end):
    with pytest.raises(ValidationError):
        BudgetScenario(ReportPeriod(start, end), Money(100), period_mode=BudgetPeriodMode.MONTH)


@pytest.mark.parametrize("factory", [budget, goal])
@pytest.mark.parametrize(
    "field,value",
    [
        ("format", True),
        ("format", 1.0),
        ("format", 2),
        ("algorithm", "unknown-v2"),
        ("kind", "deposit"),
        ("currency", {"code": "RUB", "exponent": False}),
        ("currency", {"code": "rub", "exponent": 2}),
        ("currency", {"code": "RUB", "exponent": 9}),
        ("currency", {"code": "RUB", "exponent": 2, "extra": 1}),
    ],
)
def test_planning_rejects_unknown_format_and_inexact_types(factory, field, value):
    payload = planning_payload(factory())
    payload[field] = value
    with pytest.raises(ValidationError):
        planning_from_payload(payload)


@pytest.mark.parametrize(
    "field,value",
    [
        ("limit_minor", True),
        ("limit_minor", 100.0),
        ("limit_minor", -1),
        ("limit_minor", MAX_MINOR + 1),
        ("start", "2024-2-01"),
        ("end", "2024-02-30"),
        ("period_mode", "recurring"),
        ("account_ids", [A, A]),
        ("account_ids", [B, A]),
        ("account_ids", ["invalid"]),
        ("category_ids", {}),
        ("category_ids", [1]),
    ],
)
def test_budget_payload_is_strict_and_scope_is_sorted(field, value):
    payload = planning_payload(budget())
    payload[field] = value
    with pytest.raises(ValidationError):
        planning_from_payload(payload)


@pytest.mark.parametrize(
    "field,value",
    [
        ("target_minor", 0),
        ("current_minor", -1),
        ("monthly_minor", -1),
        ("monthly_minor", 1.5),
        ("deadline", "2026-01-30"),
        ("deadline", "2026-02-30"),
        ("as_of", "20260131"),
    ],
)
def test_goal_payload_is_strict(field, value):
    payload = planning_payload(goal())
    payload[field] = value
    with pytest.raises(ValidationError):
        planning_from_payload(payload)


@pytest.mark.parametrize("factory", [budget, goal])
def test_extra_missing_and_cross_kind_fields_are_rejected(factory):
    original = planning_payload(factory())
    extra = deepcopy(original)
    extra["unexpected"] = "secret"
    missing = deepcopy(original)
    del missing["currency"]
    changed = deepcopy(original)
    changed["kind"] = "savings_goal" if original["kind"] == "budget" else "budget"
    for payload in (extra, missing, changed):
        with pytest.raises(ValidationError):
            planning_from_payload(payload)


@pytest.mark.parametrize(
    "text",
    [
        "",
        "[]",
        "null",
        "42",
        "{",
        '{"format":1,"format":1}',
        '{"kind":"budget","limit_minor":NaN}',
        '{"kind":"budget","limit_minor":Infinity}',
        "\ud800",
        " " * (4 * 1024 * 1024 + 1),
    ],
)
def test_planning_json_rejects_invalid_encoding_and_structure(text):
    with pytest.raises(ValidationError):
        planning_from_json(text)


def test_only_canonical_json_is_accepted():
    canonical = planning_json(goal())
    for value in (" " + canonical, canonical + "\n", json.dumps(json.loads(canonical), indent=2)):
        with pytest.raises(ValidationError):
            planning_from_json(value)


def test_json_floats_and_duplicate_nested_fields_are_rejected():
    canonical = planning_json(budget())
    floated = canonical.replace('"limit_minor":150000', '"limit_minor":150000.0')
    repeated = canonical.replace('"exponent":2', '"exponent":2,"exponent":2')
    for text in (floated, repeated):
        with pytest.raises(ValidationError):
            planning_from_json(text)
