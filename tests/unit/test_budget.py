from dataclasses import replace
from datetime import date

import pytest

from personal_finance.domain.analytics import ReportPeriod, ReportScope
from personal_finance.domain.budget import budget_execution, monthly_budget
from personal_finance.domain.errors import ValidationError
from personal_finance.domain.ledger import OperationKind, OperationPart
from personal_finance.domain.models import MAX_MINOR, Currency, Money
from personal_finance.domain.periods import AccountingMonth
from tests.unit.test_analytics import FOOD, OTHER, A, B, operation

FEBRUARY = AccountingMonth(2024, 2)
FIRST = date(2024, 2, 1)
LEAP_DAY = date(2024, 2, 29)


def test_monthly_budget_uses_postings_in_calendar_month_and_selected_scope():
    prior = operation(OperationKind.EXPENSE, 10000, day=date(2024, 1, 31))
    refund = operation(
        OperationKind.REFUND,
        5000,
        day=FIRST,
        refund_of=prior.id,
        refund_of_revision_id=prior.revision_id,
    )
    split = operation(
        OperationKind.EXPENSE,
        30000,
        day=LEAP_DAY,
        parts=(OperationPart(FOOD, Money(20000)), OperationPart(OTHER, Money(10000))),
    )
    fee = operation(
        OperationKind.TRANSFER,
        40000,
        day=LEAP_DAY,
        destination_account_id=B,
        fee=Money(100),
        fee_category_id=OTHER,
    )
    other = operation(OperationKind.EXPENSE, 4000, day=LEAP_DAY)
    other = replace(other, draft=replace(other.draft, account_id=B))
    usd = Currency("USD", 2)
    operations = [
        other,
        fee,
        split,
        refund,
        prior,
        operation(OperationKind.EXPENSE, 100000, day=LEAP_DAY, currency=usd, fee=Money(0, usd)),
        operation(OperationKind.INCOME, 100000, day=LEAP_DAY),
        operation(OperationKind.TRANSFER, 1000, day=LEAP_DAY, destination_account_id=B),
        operation(OperationKind.EXPENSE, 1000, day=date(2024, 3, 1)),
        replace(operation(OperationKind.EXPENSE, 1000, day=FIRST), is_deleted=True),
    ]
    report = monthly_budget(operations, FEBRUARY, Money(25000))
    assert report.period == ReportPeriod(FIRST, LEAP_DAY)
    assert report == budget_execution(operations, report.period, Money(25000))
    assert (report.spent_minor, report.remaining_minor) == (29100, -4100)
    assert {row.operation.id: row.totals.expense_minor for row in report.sources} == {
        other.id: 4000,
        fee.id: 100,
        split.id: 30000,
        refund.id: -5000,
    }
    assert report.sources[0].operation.revision_id == refund.revision_id

    scope = ReportScope(frozenset((A,)), frozenset((FOOD,)))
    scoped = monthly_budget(operations, FEBRUARY, Money(25000), scope)
    assert scoped.scope == scope
    assert (scoped.spent_minor, scoped.remaining_minor) == (15000, 10000)
    assert [(row.operation, row.totals.expense_minor) for row in scoped.sources] == [
        (refund, -5000),
        (split, 20000),
    ]


def test_monthly_budget_preserves_empty_scope_zero_limit_and_large_exact_total():
    values = [
        operation(OperationKind.EXPENSE, MAX_MINOR, day=LEAP_DAY),
        operation(OperationKind.EXPENSE, MAX_MINOR, day=LEAP_DAY),
    ]
    empty = monthly_budget(values, FEBRUARY, Money(5), ReportScope(frozenset()))
    assert empty.sources == () and (empty.spent_minor, empty.remaining_minor) == (0, 5)
    report = monthly_budget(values, FEBRUARY, Money(0))
    assert (report.spent_minor, report.remaining_minor) == (2 * MAX_MINOR, -2 * MAX_MINOR)


def test_monthly_budget_rejects_negative_limit_and_conflicting_currency():
    with pytest.raises(ValidationError, match="неотрицательный"):
        monthly_budget([], FEBRUARY, Money(-1))
    currency = Currency("RUB", 3)
    other = operation(
        OperationKind.EXPENSE, 500, day=FIRST, currency=currency, fee=Money(0, currency)
    )
    with pytest.raises(ValidationError, match="валюты"):
        monthly_budget([other], FEBRUARY, Money(100))


@pytest.mark.parametrize(
    ("start", "end", "spent"),
    [
        (date(2026, 9, 30), date(2026, 10, 1), 3000),
        (date(2026, 10, 1), date(2026, 10, 1), 2000),
        (date(2026, 10, 2), date(2026, 10, 2), -500),
    ],
)
def test_budget_period_includes_both_boundaries_and_refunds_on_receipt(start, end, spent):
    purchase = operation(OperationKind.EXPENSE, 5000, day=date(2026, 9, 29))
    values = [
        purchase,
        operation(OperationKind.EXPENSE, 1000, day=date(2026, 9, 30)),
        operation(OperationKind.EXPENSE, 2000, day=date(2026, 10, 1)),
        operation(
            OperationKind.REFUND,
            500,
            day=date(2026, 10, 2),
            refund_of=purchase.id,
            refund_of_revision_id=purchase.revision_id,
        ),
        operation(OperationKind.EXPENSE, 100000, day=date(2026, 10, 3)),
    ]
    period = ReportPeriod(start, end)
    result = budget_execution(values, period, Money(0))
    assert result.period == period
    assert (result.spent_minor, result.remaining_minor) == (spent, -spent)
    assert all(start <= row.operation.draft.occurred_on <= end for row in result.sources)
