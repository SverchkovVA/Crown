from dataclasses import replace
from datetime import date
from uuid import uuid4

import pytest

from personal_finance.domain.analytics import ReportPeriod, ReportScope, report_sources
from personal_finance.domain.analytics_comparison import compare_periods
from personal_finance.domain.errors import ValidationError
from personal_finance.domain.ledger import OperationKind
from personal_finance.domain.models import MAX_MINOR, RUB, Currency, Money
from tests.unit.test_analytics import FOOD, OTHER, operation
from tests.unit.test_analytics import A as ACCOUNT
from tests.unit.test_analytics import B as DESTINATION
from tests.unit.test_analytics_sources import example

FIRST = ReportPeriod(date(2026, 9, 1), date(2026, 9, 30))
SECOND = ReportPeriod(date(2026, 10, 1), date(2026, 10, 31))


def flows():
    return [
        operation(OperationKind.INCOME, 100000, day=date(2026, 9, 2)),
        operation(OperationKind.INCOME, 150000),
        operation(OperationKind.EXPENSE, 20000, day=date(2026, 9, 3)),
        operation(OperationKind.EXPENSE, 10000),
    ]


def test_delta_is_second_minus_first_and_reverses_exactly():
    row = compare_periods(flows(), FIRST, SECOND).totals[0]
    assert (row.income_delta, row.expense_delta, row.difference_delta) == (50000, -10000, 60000)
    reverse = compare_periods(flows(), SECOND, FIRST).totals[0]
    assert (reverse.income_delta, reverse.expense_delta, reverse.difference_delta) == (
        -50000,
        10000,
        -60000,
    )


def test_currencies_remain_separate_and_absent_side_is_zero():
    usd = Currency("USD", 2)
    values = [
        operation(OperationKind.INCOME, 200, day=date(2026, 9, 2)),
        operation(OperationKind.INCOME, 500, currency=usd, fee=Money(0, usd)),
    ]
    rows = compare_periods(values, FIRST, SECOND).totals
    assert [r.after.currency.code for r in rows] == ["RUB", "USD"]
    assert (rows[0].before.income_minor, rows[0].after.income_minor, rows[0].income_delta) == (
        200,
        0,
        -200,
    )
    assert (rows[1].before.income_minor, rows[1].after.income_minor, rows[1].income_delta) == (
        0,
        500,
        500,
    )


def test_two_empty_periods_do_not_invent_currency():
    result = compare_periods([], FIRST, SECOND)
    assert result.totals == result.categories == ()


def test_equal_and_overlapping_periods_keep_the_same_source_versions():
    value = operation(OperationKind.INCOME, 100)
    same = compare_periods([value], SECOND, SECOND)
    assert same.totals[0].income_delta == 0
    assert same.before.sources[0] is same.after.sources[0] is value
    overlap = compare_periods([value], ReportPeriod(date(2026, 9, 1), date(2026, 10, 20)), SECOND)
    assert overlap.totals[0].income_delta == 0


def test_periods_have_inclusive_bounds_without_daily_normalisation():
    values = [
        operation(OperationKind.INCOME, 30000, day=date(2026, 9, 30)),
        operation(OperationKind.INCOME, 4000, day=date(2026, 10, 1)),
    ]
    result = compare_periods(values, FIRST, ReportPeriod(date(2026, 10, 1), date(2026, 10, 2)))
    assert result.totals[0].income_delta == -26000
    assert len(result.before.sources) == len(result.after.sources) == 1


def test_category_only_on_one_side_has_explicit_zero():
    result = compare_periods([operation(OperationKind.EXPENSE, 100)], FIRST, SECOND)
    row = next(v.row for v in result.categories if v.category_id == FOOD)
    assert row.before.expense_minor == 0 and row.after.expense_minor == row.expense_delta == 100


def test_category_scope_keeps_parts_and_negative_refund_in_both_periods():
    scope = ReportScope(category_ids=frozenset({FOOD}))
    result = compare_periods(
        example().sources, SECOND, ReportPeriod(date(2026, 11, 1), date(2026, 11, 30)), scope
    )
    row = result.totals[0]
    assert result.before.scope == result.after.scope == scope
    assert (row.before.expense_minor, row.after.expense_minor, row.expense_delta) == (
        20000,
        -5000,
        -25000,
    )
    assert result.before.operation_count == result.after.operation_count == 1
    before = report_sources(result.before, RUB)[0]
    assert before.operation.draft.amount.minor == 30000 and before.totals.expense_minor == 20000
    assert report_sources(result.after, RUB)[0].totals.expense_minor == -5000


def test_account_scope_does_not_move_fee_to_transfer_destination():
    values = [
        operation(OperationKind.OPENING, 990000),
        operation(
            OperationKind.TRANSFER,
            40000,
            destination_account_id=DESTINATION,
            fee=Money(100),
            fee_category_id=OTHER,
        ),
    ]
    source = compare_periods(values, FIRST, SECOND, ReportScope(account_ids=frozenset({ACCOUNT})))
    assert source.totals[0].income_delta == 0 and source.totals[0].expense_delta == 100
    destination = compare_periods(
        values, FIRST, SECOND, ReportScope(account_ids=frozenset({DESTINATION}))
    )
    assert destination.totals == destination.categories == ()


@pytest.mark.parametrize(
    "scope",
    [
        ReportScope(account_ids=frozenset()),
        ReportScope(category_ids=frozenset()),
        ReportScope(category_ids=frozenset({str(uuid4())})),
    ],
)
def test_empty_or_unmatched_scope_does_not_expand_either_period(scope):
    result = compare_periods(flows(), FIRST, SECOND, scope)
    assert result.totals == result.before.sources == result.after.sources == ()


def test_duplicate_current_versions_are_rejected_before_comparison():
    value = operation(OperationKind.INCOME, 100)
    revised = replace(
        value, revision_id=str(uuid4()), draft=replace(value.draft, amount=Money(200))
    )
    for values in ([value, value], [value, revised]):
        with pytest.raises(ValidationError):
            compare_periods(values, FIRST, SECOND)


def test_incompatible_definition_of_one_currency_across_periods_is_rejected():
    usd2, usd3 = Currency("USD", 2), Currency("USD", 3)
    values = [
        operation(
            OperationKind.INCOME, 100, currency=usd2, fee=Money(0, usd2), day=date(2026, 9, 2)
        ),
        operation(OperationKind.INCOME, 200, currency=usd3, fee=Money(0, usd3)),
    ]
    with pytest.raises(ValidationError, match="валюты"):
        compare_periods(values, FIRST, SECOND)


def test_aggregate_delta_above_storage_limit_is_an_exact_int():
    row = compare_periods(
        [operation(OperationKind.INCOME, MAX_MINOR) for _ in range(3)], FIRST, SECOND
    ).totals[0]
    assert type(row.income_delta) is int and row.income_delta == 3 * MAX_MINOR


def test_deleted_operations_do_not_contribute_to_either_period():
    value = replace(operation(OperationKind.INCOME, 100), is_deleted=True)
    result = compare_periods([value], FIRST, SECOND)
    assert result.totals == result.before.sources == result.after.sources == ()
