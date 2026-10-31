from dataclasses import replace
from datetime import date
from uuid import uuid4

import pytest

from personal_finance.domain.analytics import ReportPeriod, income_expense_report, report_sources
from personal_finance.domain.errors import ValidationError
from personal_finance.domain.ledger import OperationKind, OperationPart
from personal_finance.domain.models import RUB, Currency, Money
from tests.unit.test_analytics import FOOD, OTHER, PERIOD, B, operation


def example():
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
    return income_expense_report(
        [
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
        ],
        PERIOD,
    )


def test_source_rows_reproduce_currency_category_and_month_totals():
    report = example()
    rows = report_sources(report, RUB)
    assert len(rows) == 4 and sum(v.totals.income_minor for v in rows) == 100000
    assert sum(v.totals.expense_minor for v in rows) == 25100
    for category in report.categories:
        selected = report_sources(report, RUB, category_id=category.category_id)
        assert sum(v.totals.income_minor for v in selected) == category.totals.income_minor
        assert sum(v.totals.expense_minor for v in selected) == category.totals.expense_minor
    for month in report.months:
        selected = report_sources(report, RUB, month=month.month)
        assert sum(v.totals.income_minor for v in selected) == month.totals.income_minor
        assert sum(v.totals.expense_minor for v in selected) == month.totals.expense_minor


def test_category_contribution_is_a_part_and_not_the_whole_operation():
    rows = report_sources(example(), RUB, category_id=FOOD, month=date(2026, 10, 1))
    assert len(rows) == 1 and rows[0].operation.draft.amount.minor == 30000
    assert rows[0].totals.expense_minor == 20000


def test_transfer_contributes_only_fee_and_refund_remains_negative_expense():
    rows = report_sources(example(), RUB)
    transfer = next(v for v in rows if v.operation.draft.kind == OperationKind.TRANSFER)
    refund = next(v for v in rows if v.operation.draft.kind == OperationKind.REFUND)
    assert transfer.operation.draft.amount.minor == 40000 and transfer.totals.expense_minor == 100
    assert refund.totals.income_minor == 0 and refund.totals.expense_minor == -5000


def test_source_versions_are_frozen_with_the_report_and_not_replaced_by_later_edit():
    value = operation(OperationKind.INCOME, 100)
    report = income_expense_report([value], PERIOD)
    changed = replace(
        value, revision_id=str(uuid4()), draft=replace(value.draft, amount=Money(200))
    )
    assert report_sources(report, RUB)[0].operation == value
    assert report_sources(income_expense_report([changed], PERIOD), RUB)[0].operation == changed
    assert report.operation_references == ((value.id, value.revision_id),)


def test_source_selection_keeps_currencies_separate():
    usd = Currency("USD", 2)
    values = [
        operation(OperationKind.INCOME, 100),
        operation(OperationKind.INCOME, 200, currency=usd, fee=Money(0, usd)),
    ]
    report = income_expense_report(values, PERIOD)
    assert len(report_sources(report, RUB)) == len(report_sources(report, usd)) == 1
    assert report_sources(report, usd)[0].totals.income_minor == 200


def test_empty_category_or_month_selection_does_not_invent_a_source():
    report = example()
    assert report_sources(report, RUB, category_id=str(uuid4())) == ()
    assert report_sources(report, RUB, month=date(2025, 1, 1)) == ()


@pytest.mark.parametrize("currency,month", [(Currency("USD", 2), None), (RUB, date(2026, 10, 15))])
def test_invalid_selection_is_refused(currency, month):
    with pytest.raises(ValidationError):
        report_sources(example(), currency, month=month)


def test_refund_source_can_exist_without_the_original_purchase_in_this_period():
    expense = operation(OperationKind.EXPENSE, 1000, day=date(2026, 9, 1))
    refund = operation(
        OperationKind.REFUND, 200, refund_of=expense.id, refund_of_revision_id=expense.revision_id
    )
    report = income_expense_report(
        [expense, refund], ReportPeriod(date(2026, 10, 1), date(2026, 10, 31))
    )
    assert report_sources(report, RUB)[0].operation == refund and len(report.sources) == 1
