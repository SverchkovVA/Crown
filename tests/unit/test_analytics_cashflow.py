from dataclasses import replace
from datetime import date
from uuid import uuid4

import pytest

from personal_finance.domain.analytics import ReportPeriod, ReportScope
from personal_finance.domain.analytics_cashflow import (
    CashSourceKind,
    cash_flow_report,
    cash_flow_sources,
)
from personal_finance.domain.errors import ValidationError
from personal_finance.domain.ledger import OperationKind, OperationPart
from personal_finance.domain.models import MAX_MINOR, RUB, Currency, Money
from tests.unit.test_analytics import FOOD, OTHER, A, B, operation

PERIOD = ReportPeriod(date(2026, 10, 1), date(2026, 10, 31))


def snapshot():
    expense = operation(
        OperationKind.EXPENSE,
        20000,
        parts=(OperationPart(FOOD, Money(13000)), OperationPart(OTHER, Money(7000))),
    )
    capital = operation(OperationKind.OPENING, 10000, day=date(2026, 10, 5))
    capital = replace(capital, draft=replace(capital.draft, account_id=B))
    refund = operation(
        OperationKind.REFUND, 5000, refund_of=expense.id, refund_of_revision_id=expense.revision_id
    )
    refund = replace(refund, draft=replace(refund.draft, account_id=B))
    return [
        operation(OperationKind.OPENING, 100000, day=date(2026, 9, 30)),
        operation(OperationKind.INCOME, 50000),
        expense,
        operation(
            OperationKind.TRANSFER,
            30000,
            destination_account_id=B,
            fee=Money(100),
            fee_category_id=OTHER,
        ),
        capital,
        refund,
    ]


def values(total):
    return (
        total.opening_minor,
        total.capital_minor,
        total.incoming_minor,
        total.outgoing_minor,
        total.change_minor,
        total.closing_minor,
    )


def test_cash_flow_uses_asset_postings_and_keeps_capital_separate():
    operations = snapshot()
    report = cash_flow_report(operations, PERIOD)
    assert values(report.totals[0]) == (100000, 10000, 85000, 50100, 44900, 144900)
    by_account = {v.account_id: v.totals for v in report.accounts}
    assert values(by_account[A]) == (100000, 0, 50000, 50100, -100, 99900)
    assert values(by_account[B]) == (0, 10000, 35000, 0, 45000, 45000)
    assert (
        report.period_operation_count == 5
        and len(report.operation_references) == 6
        and len(report.movements) == 7
    )
    assert report == cash_flow_report(list(reversed(operations)), PERIOD)


def test_transfer_destination_is_included_and_empty_scope_does_not_expand():
    report = cash_flow_report(snapshot(), PERIOD, ReportScope(account_ids=frozenset({B})))
    assert values(report.totals[0]) == (0, 10000, 35000, 0, 45000, 45000)
    assert all(v.account_id == B for v in report.movements)
    assert cash_flow_report(snapshot(), PERIOD, ReportScope(account_ids=frozenset())).totals == ()


def test_sources_explain_start_change_and_end_of_same_snapshot():
    report = cash_flow_report(snapshot(), PERIOD)
    for kind, expected in [
        (CashSourceKind.BEFORE, 100000),
        (CashSourceKind.PERIOD, 44900),
        (CashSourceKind.THROUGH_END, 144900),
    ]:
        lines = cash_flow_sources(report, RUB, kind)
        assert sum(v.minor for v in lines) == expected
    assert (
        sum(
            v.minor
            for v in cash_flow_sources(report, RUB, CashSourceKind.THROUGH_END, account_id=B)
        )
        == 45000
    )


def test_boundaries_deleted_future_and_negative_capital():
    values_ = [
        operation(OperationKind.OPENING, -100, day=PERIOD.start),
        operation(OperationKind.INCOME, 30, day=PERIOD.end),
        operation(OperationKind.INCOME, 99, day=date(2026, 11, 1)),
        replace(operation(OperationKind.EXPENSE, 55), is_deleted=True),
    ]
    report = cash_flow_report(values_, PERIOD)
    assert values(report.totals[0]) == (0, -100, 30, 0, -70, -70)
    assert report.period_operation_count == 2


def test_old_balance_without_period_activity_and_date_min_are_supported():
    report = cash_flow_report([operation(OperationKind.OPENING, 400, day=date(2026, 9, 1))], PERIOD)
    assert values(report.totals[0]) == (400, 0, 0, 0, 0, 400) and report.period_operation_count == 0
    assert cash_flow_sources(report, RUB, CashSourceKind.PERIOD) == ()
    minimum = ReportPeriod(date.min, date.min)
    assert (
        cash_flow_report([operation(OperationKind.OPENING, 0, day=date.min)], minimum)
        .totals[0]
        .closing_minor
        == 0
    )


def test_currency_and_account_definitions_cannot_be_mixed():
    usd = Currency("USD", 2)
    foreign = operation(OperationKind.INCOME, 200, currency=usd, fee=Money(0, usd))
    foreign = replace(foreign, draft=replace(foreign.draft, account_id=B))
    report = cash_flow_report([operation(OperationKind.INCOME, 100), foreign], PERIOD)
    assert {v.currency.code: v.closing_minor for v in report.totals} == {"RUB": 100, "USD": 200}
    with pytest.raises(ValidationError):
        cash_flow_report(
            [
                operation(OperationKind.INCOME, 100),
                replace(foreign, draft=replace(foreign.draft, account_id=A)),
            ],
            PERIOD,
        )
    other = Currency("RUB", 0)
    with pytest.raises(ValidationError):
        cash_flow_report(
            [
                operation(OperationKind.INCOME, 100),
                replace(
                    foreign,
                    draft=replace(foreign.draft, amount=Money(100, other), fee=Money(0, other)),
                ),
            ],
            PERIOD,
        )


def test_exact_aggregates_can_exceed_int64_without_float():
    first = operation(OperationKind.INCOME, MAX_MINOR)
    second = operation(OperationKind.INCOME, MAX_MINOR)
    second = replace(second, draft=replace(second.draft, account_id=B))
    report = cash_flow_report([first, second], PERIOD)
    assert report.totals[0].incoming_minor == report.totals[0].closing_minor == 2 * MAX_MINOR
    assert all(v.totals.closing_minor == MAX_MINOR for v in report.accounts)
    assert type(report.totals[0].closing_minor) is int


def test_duplicate_heads_invalid_scope_and_wrong_sources_are_rejected():
    value = operation(OperationKind.INCOME, 100)
    with pytest.raises(ValidationError):
        cash_flow_report([value, value], PERIOD)
    with pytest.raises(ValidationError):
        cash_flow_report([value], PERIOD, ReportScope(category_ids=frozenset({FOOD})))
    report = cash_flow_report([value], PERIOD)
    with pytest.raises(ValidationError):
        cash_flow_sources(report, Currency("USD", 2), CashSourceKind.PERIOD)
    with pytest.raises(ValidationError):
        cash_flow_sources(report, RUB, CashSourceKind.PERIOD, account_id=str(uuid4()))
    with pytest.raises(ValidationError):
        cash_flow_sources(report, RUB, "period")


def test_internal_transfer_keeps_both_legs_but_only_fee_changes_total_balance():
    transfer = operation(
        OperationKind.TRANSFER,
        30000,
        destination_account_id=B,
        fee=Money(100),
        fee_category_id=OTHER,
    )
    report = cash_flow_report([transfer], PERIOD)
    assert values(report.totals[0]) == (0, 0, 30000, 30100, -100, -100)
    assert report.period_operation_count == 1 and len(report.operation_references) == 1
    assert sorted(v.fee_minor for v in report.movements) == [0, 100]
    assert sorted(v.minor for v in cash_flow_sources(report, RUB, CashSourceKind.PERIOD)) == [
        -30100,
        30000,
    ]


def test_report_keeps_original_versions_when_input_list_is_changed():
    operations = [operation(OperationKind.INCOME, 100)]
    report = cash_flow_report(operations, PERIOD)
    first = operations[0]
    operations[0] = replace(first, draft=replace(first.draft, amount=Money(200)))
    assert (
        report.totals[0].closing_minor == 100
        and cash_flow_report(operations, PERIOD).totals[0].closing_minor == 200
    )
    assert cash_flow_sources(report, RUB, CashSourceKind.THROUGH_END)[0].operation is first
