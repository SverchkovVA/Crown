from dataclasses import FrozenInstanceError, replace
from datetime import date
from uuid import uuid4

import pytest

from personal_finance.domain.analytics import ReportPeriod
from personal_finance.domain.analytics_cashflow import CashSourceKind, cash_flow_report
from personal_finance.domain.analytics_cashflow_months import cash_month_sources, cash_months
from personal_finance.domain.errors import ValidationError
from personal_finance.domain.ledger import OperationKind
from personal_finance.domain.models import MAX_MINOR, RUB, Currency, Money
from tests.unit.test_analytics import B, operation
from tests.unit.test_analytics_cashflow import snapshot


def report():
    return cash_flow_report(snapshot(), ReportPeriod(date(2026, 9, 15), date(2026, 12, 15)))


def test_months_keep_empty_months_and_reconcile_each_source_part():
    value = report()
    page = cash_months(value, RUB)
    assert page.month_count == 4 and len(page.rows) == 4 and page.report is value
    assert [
        (
            v.totals.opening_minor,
            v.totals.capital_minor,
            v.totals.incoming_minor,
            v.totals.outgoing_minor,
            v.totals.closing_minor,
        )
        for v in page.rows
    ] == [
        (0, 100000, 0, 0, 100000),
        (100000, 10000, 85000, 50100, 144900),
        (144900, 0, 0, 0, 144900),
        (144900, 0, 0, 0, 144900),
    ]
    assert page.rows[0].period.start == date(2026, 9, 15) and page.rows[-1].period.end == date(
        2026, 12, 15
    )
    for row, v in enumerate(page.rows):
        for kind, expected in [
            (CashSourceKind.BEFORE, v.totals.opening_minor),
            (CashSourceKind.PERIOD, v.totals.change_minor),
            (CashSourceKind.THROUGH_END, v.totals.closing_minor),
        ]:
            assert sum(line.minor for line in cash_month_sources(page, row, kind)) == expected
    assert (page.lower_minor, page.upper_minor) == (0, 144900)


def test_page_offset_retains_prior_opening_and_common_scale():
    value = report()
    full = cash_months(value, RUB)
    for offset in range(4):
        page = cash_months(value, RUB, offset=offset, limit=1)
        assert page.rows == (full.rows[offset],)
        assert (page.lower_minor, page.upper_minor) == (full.lower_minor, full.upper_minor)


def test_partial_period_excludes_earlier_capital_from_month_movement():
    page = cash_months(
        cash_flow_report(snapshot(), ReportPeriod(date(2026, 10, 10), date(2026, 10, 17))), RUB
    )
    assert page.rows[0].period == ReportPeriod(date(2026, 10, 10), date(2026, 10, 17))
    total = page.rows[0].totals
    assert (total.opening_minor, total.capital_minor, total.change_minor, total.closing_minor) == (
        110000,
        0,
        34900,
        144900,
    )


def test_account_destination_includes_transfer_without_sender_fee():
    page = cash_months(report(), RUB, account_id=B)
    total = page.rows[1].totals
    assert (
        total.capital_minor,
        total.incoming_minor,
        total.outgoing_minor,
        total.closing_minor,
    ) == (10000, 35000, 0, 45000)
    lines = cash_month_sources(page, 1, CashSourceKind.PERIOD)
    transfer = next(v for v in lines if v.operation.draft.kind == OperationKind.TRANSFER)
    assert transfer.account_id == B and transfer.fee_minor == 0
    assert all(v.account_id == B for v in cash_month_sources(page, 3, CashSourceKind.THROUGH_END))


def test_currencies_and_large_int_totals_are_separate():
    usd = Currency("USD", 2)
    foreign_id = str(uuid4())
    foreign = operation(OperationKind.OPENING, 500, currency=usd, fee=Money(0, usd))
    foreign = replace(foreign, draft=replace(foreign.draft, account_id=foreign_id))
    value = cash_flow_report(
        [*snapshot(), foreign], ReportPeriod(date(2026, 10, 1), date(2026, 10, 31))
    )
    assert cash_months(value, usd).rows[0].totals.closing_minor == 500
    assert cash_months(value, RUB).rows[0].totals.closing_minor == 144900
    first = operation(OperationKind.OPENING, MAX_MINOR)
    second = operation(OperationKind.OPENING, MAX_MINOR)
    second = replace(second, draft=replace(second.draft, account_id=B))
    page = cash_months(cash_flow_report([first, second], value.period), RUB)
    assert page.rows[0].totals.closing_minor == 2 * MAX_MINOR


def test_calendar_extremes_build_only_requested_months():
    value = cash_flow_report(snapshot(), ReportPeriod(date.min, date.max))
    first = cash_months(value, RUB)
    assert first.month_count == 119988 and len(first.rows) == 6
    assert first.rows[0].period.start == date.min and first.rows[0].totals.closing_minor == 0
    last = cash_months(value, RUB, offset=119987, limit=24)
    assert len(last.rows) == 1 and last.rows[0].period.end == date.max
    assert last.rows[0].totals.closing_minor == 144900
    assert sum(v.minor for v in cash_month_sources(last, 0, CashSourceKind.BEFORE)) == 144900


def test_negative_capital_and_deleted_future_and_immutable_rows():
    negative = operation(OperationKind.OPENING, -100, day=date(2026, 9, 30))
    future = operation(OperationKind.INCOME, 900, day=date(2027, 1, 1))
    deleted = replace(operation(OperationKind.INCOME, 800), is_deleted=True)
    value = cash_flow_report(
        [negative, future, deleted], ReportPeriod(date(2026, 10, 1), date(2026, 11, 30))
    )
    page = cash_months(value, RUB)
    assert all(v.totals.closing_minor == -100 for v in page.rows)
    assert (page.lower_minor, page.upper_minor) == (-100, -100)
    with pytest.raises(FrozenInstanceError):
        page.rows = ()


@pytest.mark.parametrize(
    "changes",
    [
        {"offset": -1},
        {"offset": 4},
        {"offset": True},
        {"offset": "1"},
        {"limit": 0},
        {"limit": 25},
        {"limit": True},
        {"limit": "6"},
    ],
)
def test_invalid_page_rejected(changes):
    with pytest.raises(ValidationError):
        cash_months(report(), RUB, **changes)


def test_invalid_currency_account_and_source_selection_do_not_fallback():
    value = report()
    for kwargs in [{"currency": Currency("USD", 2)}, {"currency": RUB, "account_id": str(uuid4())}]:
        with pytest.raises(ValidationError):
            cash_months(value, **kwargs)
    page = cash_months(value, RUB)
    for row, kind in [
        (-1, CashSourceKind.PERIOD),
        (True, CashSourceKind.PERIOD),
        (4, CashSourceKind.PERIOD),
        (0, "period"),
    ]:
        with pytest.raises(ValidationError):
            cash_month_sources(page, row, kind)
    with pytest.raises(ValidationError):
        cash_months(None, RUB)
