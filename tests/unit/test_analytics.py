from dataclasses import replace
from datetime import date, datetime
from uuid import uuid4

import pytest

from personal_finance.domain.analytics import ReportPeriod, income_expense_report
from personal_finance.domain.errors import ValidationError
from personal_finance.domain.ledger import Operation, OperationDraft, OperationKind, OperationPart
from personal_finance.domain.models import MAX_MINOR, RUB, Currency, Money

A, B, INCOME, FOOD, OTHER = (str(uuid4()) for _ in range(5))
PERIOD = ReportPeriod(date(2026, 10, 1), date(2026, 11, 30))


def operation(kind, minor, *, day=date(2026, 10, 15), category=None, **kwargs):
    if category is None and not kwargs.get("parts"):
        category = (
            INCOME
            if kind == OperationKind.INCOME
            else FOOD
            if kind in (OperationKind.EXPENSE, OperationKind.REFUND)
            else None
        )
    currency = kwargs.pop("currency", RUB)
    return Operation(
        str(uuid4()),
        str(uuid4()),
        OperationDraft(kind, day, A, Money(minor, currency), category, **kwargs),
    )


def test_income_expense_split_transfer_fee_and_refund_follow_existing_postings():
    expense = operation(
        OperationKind.EXPENSE,
        30000,
        parts=(OperationPart(FOOD, Money(20000)), OperationPart(OTHER, Money(10000))),
    )
    refund = operation(
        OperationKind.REFUND,
        5000,
        day=date(2026, 11, 2),
        refund_of=expense.id,
        refund_of_revision_id=expense.revision_id,
    )
    values = [
        operation(OperationKind.OPENING, 990000),
        operation(OperationKind.INCOME, 100000),
        expense,
        operation(
            OperationKind.TRANSFER,
            40000,
            destination_account_id=B,
            fee=Money(100),
            fee_category_id=OTHER,
        ),
        refund,
    ]
    report = income_expense_report(values, PERIOD)
    assert report.operation_count == 4
    (total,) = report.totals
    assert (total.income_minor, total.expense_minor, total.difference_minor) == (
        100000,
        25100,
        74900,
    )
    categories = {v.category_id: v.totals for v in report.categories}
    assert categories[FOOD].expense_minor == 15000 and categories[OTHER].expense_minor == 10100
    assert [(v.month, v.totals.expense_minor) for v in report.months] == [
        (date(2026, 10, 1), 30100),
        (date(2026, 11, 1), -5000),
    ]
    assert sum(v.totals.expense_minor for v in report.categories) == total.expense_minor
    assert sum(v.totals.income_minor for v in report.months) == total.income_minor
    assert report == income_expense_report(list(reversed(values)), PERIOD)


def test_transfer_without_fee_and_opening_do_not_look_like_income_or_expense():
    values = [
        operation(OperationKind.OPENING, -990000),
        operation(OperationKind.TRANSFER, 1200, destination_account_id=B),
    ]
    assert not income_expense_report(values, PERIOD).totals
    assert income_expense_report(values, PERIOD).operation_count == 0


def test_deleted_versions_and_outside_dates_are_excluded_and_boundaries_included():
    values = [
        operation(OperationKind.INCOME, 100, day=PERIOD.start),
        operation(OperationKind.EXPENSE, 30, day=PERIOD.end),
        operation(OperationKind.EXPENSE, 50, day=date(2026, 9, 30)),
        replace(operation(OperationKind.INCOME, 999), is_deleted=True),
    ]
    r = income_expense_report(values, PERIOD)
    assert r.operation_count == 2 and r.totals[0].difference_minor == 70


def test_refund_of_an_older_purchase_reduces_expense_on_return_date():
    expense = operation(OperationKind.EXPENSE, 1000, day=date(2026, 9, 1))
    refund = operation(
        OperationKind.REFUND, 200, refund_of=expense.id, refund_of_revision_id=expense.revision_id
    )
    r = income_expense_report([expense, refund], PERIOD)
    assert r.totals[0].income_minor == 0 and r.totals[0].expense_minor == -200


def test_currencies_are_separate_and_never_converted_or_added_together():
    usd = Currency("USD", 2)
    r = income_expense_report(
        [
            operation(OperationKind.INCOME, 100),
            operation(OperationKind.INCOME, 200, currency=usd, fee=Money(0, usd)),
        ],
        PERIOD,
    )
    assert [(v.currency.code, v.income_minor) for v in r.totals] == [("RUB", 100), ("USD", 200)]


def test_exact_totals_may_exceed_one_stored_amount_without_overflow_or_float():
    r = income_expense_report(
        [operation(OperationKind.INCOME, MAX_MINOR), operation(OperationKind.INCOME, MAX_MINOR)],
        PERIOD,
    )
    assert r.totals[0].income_minor == 2 * MAX_MINOR and type(r.totals[0].income_minor) is int


def test_different_definitions_of_the_same_currency_are_refused():
    other = Currency("RUB", 3)
    with pytest.raises(ValidationError, match="валюты"):
        income_expense_report(
            [
                operation(OperationKind.INCOME, 1),
                operation(OperationKind.INCOME, 2, currency=other, fee=Money(0, other)),
            ],
            PERIOD,
        )


def test_history_or_duplicate_ids_cannot_double_a_report():
    value = operation(OperationKind.INCOME, 100)
    with pytest.raises(ValidationError, match="текущую версию"):
        income_expense_report([value, replace(value, revision_id=str(uuid4()))], PERIOD)


@pytest.mark.parametrize(
    "start,end",
    [
        (date(2026, 11, 2), date(2026, 10, 1)),
        (None, date(2026, 10, 1)),
        (datetime(2026, 10, 1), date(2026, 10, 2)),
    ],
)
def test_period_is_explicit_calendar_and_ordered(start, end):
    with pytest.raises(ValidationError):
        ReportPeriod(start, end)


def test_empty_report_has_no_invented_currency_or_zero_months():
    r = income_expense_report([], PERIOD)
    assert r.totals == () and r.categories == () and r.months == () and r.operation_references == ()
