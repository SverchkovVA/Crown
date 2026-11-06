"""Границы отбора и SQL-защиты кандидата; только временная зашифрованная книга."""

from dataclasses import replace
from uuid import uuid4

import pytest
from sqlalchemy import event
from sqlalchemy.exc import DatabaseError

from personal_finance.domain.analytics import ReportScope
from personal_finance.domain.errors import ValidationError
from personal_finance.domain.models import AccountKind, CategoryKind, Currency, Money
from personal_finance.domain.planning_scenarios import (
    BudgetScenario,
    PlanningKind,
    planning_json,
)
from tests.integration.test_planning_scenario_migration import snapshot
from tests.integration.test_planning_scenario_repository import storage as storage
from tests.unit.test_planning_scenarios import PERIOD, goal


def budget(scope=None):
    return BudgetScenario(PERIOD, Money(150000), ReportScope() if scope is None else scope)


def test_names_are_unique_per_kind_and_kind_cannot_change(storage):
    session, repo = storage
    first = repo.save("План", budget())
    second = repo.save("План", goal())
    assert repo.items(kind=PlanningKind.BUDGET) == [first]
    assert repo.items(kind=PlanningKind.SAVINGS_GOAL) == [second]
    before = snapshot(session.engine)
    for scenario in (budget(), goal()):
        with pytest.raises(ValidationError, match="названием"):
            repo.save("ПЛАН", scenario)
    for record, scenario in ((first, goal()), (second, budget())):
        with pytest.raises(ValidationError, match="заменить"):
            repo.save(
                "Другой", scenario, identifier=record.id, expected_revision_id=record.revision_id
            )
    assert snapshot(session.engine) == before


def test_budget_scope_retains_exact_ids_all_and_empty_without_ledger_writes(storage):
    session, repo = storage
    accounts = [session.catalog.add_account(f"Наличные {i}", AccountKind.CASH) for i in (1, 2)]
    expense = session.catalog.add_category("Особая", CategoryKind.EXPENSE)
    scope = ReportScope(frozenset(a.id for a in accounts), frozenset((expense.id,)))
    before = session.ledger.operations(), session.ledger.balances()
    records = [
        repo.save("Точный отбор", budget(scope)),
        repo.save("Все", budget()),
        repo.save("Ничего", budget(ReportScope(frozenset(), frozenset()))),
    ]
    assert [repo.get(r.id).scenario for r in records] == [
        budget(scope),
        budget(),
        budget(ReportScope(frozenset(), frozenset())),
    ]
    assert (session.ledger.operations(), session.ledger.balances()) == before
    repo.check_integrity()
    session.check_integrity()


@pytest.mark.parametrize(
    "case",
    [
        "missing_account",
        "category_as_account",
        "income",
        "account_as_category",
        "currency",
        "precision",
    ],
)
def test_invalid_scope_fails_before_any_write(storage, case):
    session, repo = storage
    account = session.catalog.add_account("Наличные", AccountKind.CASH)
    expense = session.catalog.add_category("Расход", CategoryKind.EXPENSE)
    income = session.catalog.add_category("Доход", CategoryKind.INCOME)
    account_id = {
        "missing_account": str(uuid4()),
        "category_as_account": expense.id,
    }.get(case, account.id)
    category_id = {"income": income.id, "account_as_category": account.id}.get(case, expense.id)
    scenario = budget(ReportScope(frozenset((account_id,)), frozenset((category_id,))))
    if case == "currency":
        scenario = replace(scenario, limit=Money(100, Currency("USD", 2)))
    if case == "precision":
        scenario = replace(scenario, limit=Money(100, Currency("RUB", 3)))
    before = snapshot(session.engine)
    with pytest.raises(ValidationError, match="этой книги"):
        repo.save("Не сохранять", scenario)
    assert snapshot(session.engine) == before


def test_copy_has_own_id_and_history(storage):
    _, repo = storage
    original = repo.save("Оригинал", budget())
    copied = repo.save("Копия", original.scenario)
    assert copied.id != original.id and copied.revision_id != original.revision_id
    assert copied.scenario == original.scenario
    assert repo.history(original.id) == [original]
    assert repo.history(copied.id) == [copied]


@pytest.mark.parametrize("kind", ["budget", "savings_goal"])
def test_raw_payload_subtype_change_is_refused_when_sealing(storage, kind):
    session, repo = storage
    first = repo.save("План", budget() if kind == "budget" else goal())
    other = goal() if kind == "budget" else budget()
    before = snapshot(session.engine)
    changed = []

    def substitute(connection, cursor, statement, parameters, context, many):
        if statement.startswith("INSERT INTO planning_scenario_revisions"):
            revision = parameters[0]
            connection.exec_driver_sql(
                "UPDATE planning_scenario_revisions SET scenario_kind=?,scenario_json=? "
                "WHERE revision_id=?",
                ("savings_goal" if kind == "budget" else "budget", planning_json(other), revision),
            )
            changed.append(True)

    event.listen(session.engine, "after_cursor_execute", substitute)
    try:
        with pytest.raises(DatabaseError, match="type is immutable"):
            repo.save(
                "План", first.scenario, identifier=first.id, expected_revision_id=first.revision_id
            )
    finally:
        event.remove(session.engine, "after_cursor_execute", substitute)
    assert changed == [True]
    assert snapshot(session.engine) == before


@pytest.mark.parametrize("field", ["account_ids", "category_ids"])
def test_raw_scope_substitution_is_refused_at_sealing(storage, field):
    session, repo = storage
    invalid = str(uuid4())
    scope = ReportScope(
        frozenset((invalid,)) if field == "account_ids" else None,
        frozenset((invalid,)) if field == "category_ids" else None,
    )
    changed = []
    before = snapshot(session.engine)

    def substitute(connection, cursor, statement, parameters, context, many):
        if statement.startswith("INSERT INTO planning_scenario_revisions"):
            connection.exec_driver_sql(
                "UPDATE planning_scenario_revisions SET scenario_json=? WHERE revision_id=?",
                (planning_json(budget(scope)), parameters[0]),
            )
            changed.append(True)

    event.listen(session.engine, "after_cursor_execute", substitute)
    try:
        with pytest.raises(DatabaseError, match="planning scope"):
            repo.save("Не сохранять", budget())
    finally:
        event.remove(session.engine, "after_cursor_execute", substitute)
    assert changed == [True]
    assert snapshot(session.engine) == before


@pytest.mark.parametrize(
    "options",
    [
        {"kind": "budget"},
        {"kind": True},
        {"include_archived": 1},
    ],
)
def test_selection_flags_have_exact_types(storage, options):
    _, repo = storage
    with pytest.raises(ValidationError):
        repo.items(**options)
