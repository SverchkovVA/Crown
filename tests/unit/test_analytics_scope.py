from dataclasses import replace
from uuid import uuid4

import pytest

from personal_finance.domain.analytics import (
    ReportPeriod,
    ReportScope,
    income_expense_report,
    report_sources,
)
from personal_finance.domain.errors import ValidationError
from personal_finance.domain.models import RUB
from tests.unit.test_analytics import FOOD, OTHER, A, B, OperationKind, operation
from tests.unit.test_analytics import PERIOD as ORIGINAL_PERIOD
from tests.unit.test_analytics_sources import example

PERIOD = ReportPeriod(ORIGINAL_PERIOD.start, ORIGINAL_PERIOD.end)


def values():
    return example().sources


def test_category_filter_keeps_only_part_and_refund_in_all_source_views():
    report = income_expense_report(values(), PERIOD, ReportScope(category_ids=frozenset({FOOD})))
    assert report.operation_count == 2 and report.totals[0].expense_minor == 15000
    rows = report_sources(report, RUB)
    assert sum(v.totals.expense_minor for v in rows) == 15000
    assert next(v for v in rows if v.totals.expense_minor > 0).operation.draft.amount.minor == 30000
    assert report_sources(report, RUB, category_id=OTHER) == ()


def test_multiple_categories_sum_each_part_once():
    report = income_expense_report(
        values(), PERIOD, ReportScope(category_ids=frozenset({FOOD, OTHER}))
    )
    assert report.operation_count == 3 and report.totals[0].expense_minor == 25100
    assert report.totals[0].income_minor == 0


def test_account_selection_charges_transfer_fee_only_to_its_source():
    another = operation(OperationKind.INCOME, 2000)
    another = replace(another, draft=replace(another.draft, account_id=B))
    report = income_expense_report(
        [*values(), another], PERIOD, ReportScope(account_ids=frozenset({B}))
    )
    assert report.operation_count == 1
    assert report.totals[0].income_minor == 2000 and report.totals[0].expense_minor == 0


def test_empty_account_or_category_filter_means_none_not_all():
    for scope in (ReportScope(account_ids=frozenset()), ReportScope(category_ids=frozenset())):
        report = income_expense_report(values(), PERIOD, scope)
        assert report.sources == report.totals == report.months == report.categories == ()


def test_account_and_category_filters_intersect():
    report = income_expense_report(
        values(), PERIOD, ReportScope(frozenset({A}), frozenset({OTHER}))
    )
    assert report.operation_count == 2 and report.totals[0].expense_minor == 10100
    assert sum(v.totals.expense_minor for v in report_sources(report, RUB)) == 10100


def test_unknown_valid_identifier_produces_empty_report():
    report = income_expense_report(
        values(), PERIOD, ReportScope(account_ids=frozenset({str(uuid4())}))
    )
    assert report.totals == () and report.sources == ()


@pytest.mark.parametrize("scope", [False, {"account_ids": [A]}, "all"])
def test_invalid_scope_is_rejected(scope):
    with pytest.raises(ValidationError):
        income_expense_report(values(), PERIOD, scope)


@pytest.mark.parametrize("args", [{"account_ids": {A}}, {"category_ids": frozenset({"bad"})}])
def test_scope_rejects_mutable_or_invalid_identifiers(args):
    with pytest.raises(ValidationError):
        ReportScope(**args)


def test_scoped_sources_conserve_currency_category_and_month_totals():
    report = income_expense_report(values(), PERIOD, ReportScope(category_ids=frozenset({FOOD})))
    for item in report.categories:
        rows = report_sources(report, RUB, category_id=item.category_id)
        assert sum(v.totals.expense_minor for v in rows) == item.totals.expense_minor
    for item in report.months:
        rows = report_sources(report, RUB, month=item.month)
        assert sum(v.totals.expense_minor for v in rows) == item.totals.expense_minor
