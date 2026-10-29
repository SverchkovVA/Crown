from dataclasses import replace
from datetime import date
from types import SimpleNamespace
from uuid import uuid4

import pytest
from matplotlib.backend_bases import MouseEvent, PickEvent
from PySide6.QtCore import QDate

from personal_finance.domain.analytics import ReportPeriod
from personal_finance.domain.analytics_cashflow import CashSourceKind, cash_flow_report
from personal_finance.domain.ledger import OperationKind
from personal_finance.domain.models import MAX_MINOR, RUB, Currency, Money
from personal_finance.presentation.analytics import format_report_amount
from personal_finance.presentation.analytics_cashflow_months import CashFlowMonths
from tests.unit.test_analytics import A, B, operation
from tests.unit.test_analytics_cashflow import snapshot


@pytest.fixture
def make(qtbot):
    widgets = []

    def create(report):
        widget = CashFlowMonths(report, {A: "Вымышленный основной", B: "Вымышленный резерв"})
        qtbot.addWidget(widget)
        widgets.append(widget)
        return widget

    yield create
    for widget in widgets:
        widget.dispose()


def example():
    return cash_flow_report(snapshot(), ReportPeriod(date(2026, 9, 15), date(2026, 12, 15)))


def test_exact_rows_graph_and_sources_preserve_same_snapshot(make):
    value = example()
    widget = make(value)
    assert widget.model.rowCount() == 4
    assert widget.model.data(widget.model.index(1, 6)) == "1 449,00"
    widget.canvas.draw()
    assert "1 449,00" in {v.get_text() for v in widget.axes.texts}
    widget.table.selectRow(1)
    seen = []
    widget.sources_requested.connect(lambda *args: seen.append(args))
    for kind, expected in [
        (CashSourceKind.BEFORE, 100000),
        (CashSourceKind.PERIOD, 44900),
        (CashSourceKind.THROUGH_END, 144900),
    ]:
        widget.source_buttons[kind].click()
        assert seen[-1][0] == RUB and sum(v.minor for v in seen[-1][1]) == expected
    assert widget.page.report is value


def test_real_matplotlib_pick_opens_month_closing_sources(make):
    widget = make(example())
    widget.canvas.draw()
    seen = []
    widget.sources_requested.connect(lambda *args: seen.append(args))
    artist = next(k for k, v in widget._targets.items() if v == 1)
    bbox = artist.get_window_extent()
    mouse = MouseEvent(
        "button_press_event",
        widget.canvas,
        (bbox.x0 + bbox.x1) / 2,
        (bbox.y0 + bbox.y1) / 2,
        button=1,
    )
    event = PickEvent("pick_event", widget.canvas, mouse, artist)
    widget.canvas.callbacks.process("pick_event", event)
    assert len(seen) == 1 and sum(v.minor for v in seen[0][1]) == 144900
    assert "10.2026" in seen[0][2] and widget.table.currentIndex().row() == 1


def test_clear_selection_disables_every_source_button(make):
    widget = make(example())
    seen = []
    widget.sources_requested.connect(lambda *args: seen.append(args))
    widget.table.clearSelection()
    assert all(not button.isEnabled() for button in widget.source_buttons.values())
    widget.show_sources(CashSourceKind.THROUGH_END)
    assert not seen


def test_month_paging_and_jump_keep_scale_and_old_opening(make):
    opening = operation(OperationKind.OPENING, 100, day=date(2026, 1, 1))
    income = operation(OperationKind.INCOME, 100, day=date(2026, 8, 1))
    widget = make(
        cash_flow_report([opening, income], ReportPeriod(date(2026, 1, 1), date(2026, 12, 31)))
    )
    original_limits = widget.axes.get_xlim()
    assert len(widget.page.rows) == 6 and widget.next.isEnabled()
    widget.next.click()
    assert widget.page.offset == 6 and widget.page.rows[0].totals.opening_minor == 100
    assert widget.axes.get_xlim() == original_limits
    widget.previous.click()
    assert widget.page.offset == 0
    widget.month.setDate(QDate(2026, 8, 1))
    assert widget.page.offset == 7 and widget.page.rows[0].totals.closing_minor == 200
    assert not widget.next.isEnabled()


def test_currency_account_mismatch_is_empty_without_fallback(make):
    usd = Currency("USD", 2)
    uid = str(uuid4())
    item = operation(OperationKind.OPENING, 500, currency=usd, fee=Money(0, usd))
    item = replace(item, draft=replace(item.draft, account_id=uid))
    value = cash_flow_report(
        [*snapshot(), item], ReportPeriod(date(2026, 10, 1), date(2026, 10, 31))
    )
    widget = make(value)
    widget.account.setCurrentIndex(widget.account.findData(A))
    widget.currency.setCurrentIndex(widget.currency.findData("USD"))
    assert widget.page is None and widget.account.currentData() == A
    assert widget.model.rowCount() == 0 and all(
        not b.isEnabled() for b in widget.source_buttons.values()
    )
    widget.account.setCurrentIndex(0)
    assert widget.page.currency == usd and widget.model.data(widget.model.index(0, 6)) == "5,00"


def test_large_int_negative_amounts_and_empty_snapshot(make):
    first = operation(OperationKind.OPENING, MAX_MINOR)
    second = operation(OperationKind.OPENING, MAX_MINOR)
    second = replace(second, draft=replace(second.draft, account_id=B))
    value = cash_flow_report([first, second], ReportPeriod(date(2026, 10, 1), date(2026, 10, 31)))
    widget = make(value)
    widget.canvas.draw()
    assert widget.coordinate_divisor > 1 and all(
        abs(v.get_width()) <= 10**9 for v in widget._targets
    )
    assert format_report_amount(2 * MAX_MINOR, RUB) in {v.get_text() for v in widget.axes.texts}
    negative = make(cash_flow_report([operation(OperationKind.OPENING, -100)], value.period))
    assert next(iter(negative._targets)).get_hatch() == "//"
    empty = make(cash_flow_report([], value.period))
    assert (
        empty.page is None and not empty.currency.isEnabled() and "снимке" in empty.message.text()
    )


def test_full_calendar_range_jumps_without_building_all_months(make):
    widget = make(cash_flow_report(snapshot(), ReportPeriod(date.min, date.max)))
    assert widget.page.month_count == 119988 and widget.model.rowCount() == 6
    widget.month.setDate(QDate(9999, 12, 1))
    assert widget.model.rowCount() == 1 and widget.model.data(widget.model.index(0, 0)) == "12.9999"
    assert widget.page.rows[0].totals.closing_minor == 144900


def test_dispose_disconnects_pick_and_ignores_late_render(make):
    widget = make(example())
    artist = next(iter(widget._targets))
    seen = []
    widget.sources_requested.connect(lambda *args: seen.append(args))
    widget.dispose()
    widget.dispose()
    widget._picked(SimpleNamespace(artist=artist))
    widget._render()
    assert not widget._targets and not seen


def test_invalid_account_selection_does_not_mean_all_accounts(make):
    widget = make(example())
    widget.account.setCurrentIndex(-1)
    assert widget.page is None and widget.model.rowCount() == 0
    assert all(not b.isEnabled() for b in widget.source_buttons.values())
