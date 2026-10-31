from datetime import date
from types import SimpleNamespace
from uuid import uuid4

from personal_finance.domain.analytics import MonthlyFlow, ReportPeriod, income_expense_report
from personal_finance.domain.ledger import OperationKind
from personal_finance.domain.models import MAX_MINOR, RUB, Currency, Money
from personal_finance.presentation.analytics import IncomeExpenseDialog, format_report_amount
from tests.unit.test_analytics import FOOD, OTHER, PERIOD, operation
from tests.unit.test_analytics_sources import example


def chart_dialog(qtbot, report=None):
    dialog = IncomeExpenseDialog(example() if report is None else report, [])
    qtbot.addWidget(dialog)
    return dialog, dialog.charts


def test_month_graph_uses_exact_snapshot_and_negative_refund(qtbot):
    dialog, chart = chart_dialog(qtbot)
    chart.canvas.draw()
    assert [(v.month, v.totals.expense_minor) for v in chart.plotted_rows] == [
        (date(2026, 10, 1), 30100),
        (date(2026, 11, 1), -5000),
    ]
    labels = {v.get_text() for v in chart.axes.texts}
    assert {"1 000,00", "301,00", "−50,00"} <= labels
    assert any(v.get_hatch() == "//" for v in chart.axes.patches)
    assert dialog.report == example_from_same_sources(dialog.report)


def example_from_same_sources(report):
    return income_expense_report(report.sources, report.period)


def test_category_graph_excludes_income_and_opens_only_selected_category(qtbot):
    dialog, chart = chart_dialog(qtbot)
    chart.kind.setCurrentIndex(chart.kind.findData("categories"))
    assert {v.category_id: v.totals.expense_minor for v in chart.plotted_rows} == {
        FOOD: 15000,
        OTHER: 10100,
    }
    bar = next(b for b, target in chart._targets.items() if target[0] == FOOD)
    chart._picked(SimpleNamespace(artist=bar))
    assert dialog.tabs.currentWidget() is dialog.source_panel
    assert len(dialog._source_rows) == 2
    assert sum(v.totals.expense_minor for v in dialog._source_rows) == 15000


def test_month_pick_opens_refund_from_same_snapshot(qtbot):
    dialog, chart = chart_dialog(qtbot)
    bar = next(b for b, target in chart._targets.items() if target[1] == date(2026, 11, 1))
    chart._picked(SimpleNamespace(artist=bar))
    assert len(dialog._source_rows) == 1
    assert dialog._source_rows[0].operation.draft.kind == OperationKind.REFUND
    assert dialog._source_rows[0].totals.expense_minor == -5000


def test_currency_switch_does_not_combine_money(qtbot):
    usd = Currency("USD", 2)
    report = income_expense_report(
        [
            operation(OperationKind.INCOME, 10000),
            operation(OperationKind.INCOME, 20000, currency=usd, fee=Money(0, usd)),
        ],
        PERIOD,
    )
    _, chart = chart_dialog(qtbot, report)
    chart.currency.setCurrentIndex(chart.currency.findData("USD"))
    assert len(chart.plotted_rows) == 1
    assert chart.plotted_rows[0].totals.currency == usd
    assert chart.plotted_rows[0].totals.income_minor == 20000
    assert {v.get_text() for v in chart.axes.texts} == {"200,00", "0,00"}


def test_empty_graph_has_message_and_no_fake_currency(qtbot):
    _, chart = chart_dialog(qtbot, income_expense_report([], PERIOD))
    assert chart.currency.count() == 0 and not chart.currency.isEnabled()
    assert chart.plotted_rows == () and not chart._targets
    assert "нет данных" in chart.message.text()
    assert not chart.previous.isEnabled() and not chart.next.isEnabled()


def test_income_only_report_has_no_expense_categories(qtbot):
    _, chart = chart_dialog(
        qtbot, income_expense_report([operation(OperationKind.INCOME, 10000)], PERIOD)
    )
    chart.kind.setCurrentIndex(chart.kind.findData("categories"))
    assert chart.plotted_rows == () and not chart._targets
    assert "нет данных" in chart.message.text()


def test_zero_net_expense_category_is_preserved(qtbot):
    expense = operation(OperationKind.EXPENSE, 1000)
    refund = operation(
        OperationKind.REFUND, 1000, refund_of=expense.id, refund_of_revision_id=expense.revision_id
    )
    _, chart = chart_dialog(qtbot, income_expense_report([expense, refund], PERIOD))
    chart.kind.setCurrentIndex(chart.kind.findData("categories"))
    assert len(chart.plotted_rows) == 1 and chart.plotted_rows[0].totals.expense_minor == 0
    assert [v.get_text() for v in chart.axes.texts] == ["0,00"]


def test_pages_cover_all_months_and_categories_once(qtbot):
    period = ReportPeriod(date(2025, 1, 1), date(2026, 2, 28))
    values = [
        operation(
            OperationKind.EXPENSE,
            1000 + i,
            category=str(uuid4()),
            day=date(2025 + i // 12, 1 + i % 12, 15),
        )
        for i in range(14)
    ]
    report = income_expense_report(values, period)
    _, chart = chart_dialog(qtbot, report)
    seen = []
    while True:
        seen.extend(v.month for v in chart.plotted_rows)
        if not chart.next.isEnabled():
            break
        chart.next.click()
    assert seen == [v.month for v in report.months] and len(seen) == 14
    chart.kind.setCurrentIndex(chart.kind.findData("categories"))
    assert len(chart.plotted_rows) == 8 and not chart.previous.isEnabled()
    identifiers = [v.category_id for v in chart.plotted_rows]
    chart.next.click()
    identifiers += [v.category_id for v in chart.plotted_rows]
    assert len(identifiers) == len(set(identifiers)) == 14
    assert set(identifiers) == {v.category_id for v in report.categories}
    assert not chart.next.isEnabled()


def test_repeated_renders_keep_one_pick_handler(qtbot):
    _, chart = chart_dialog(qtbot)
    events = []
    chart.sources_requested.connect(lambda *args: events.append(args))
    connection = chart._pick_connection
    for _iteration in range(4):
        chart.kind.setCurrentIndex(1)
        chart.kind.setCurrentIndex(0)
    chart._picked(SimpleNamespace(artist=next(iter(chart._targets))))
    assert len(events) == 1 and connection == chart._pick_connection
    assert events[0][0] == "RUB"


def test_closing_report_disposes_canvas_and_ignores_late_events(qtbot):
    dialog, chart = chart_dialog(qtbot)
    artist = next(iter(chart._targets))
    events = []
    chart.sources_requested.connect(lambda *args: events.append(args))
    dialog.reject()
    chart.dispose()
    chart._picked(SimpleNamespace(artist=artist))
    chart.kind.setCurrentIndex(1)
    assert chart._disposed and not chart.figure.axes and not chart._targets and not events


def test_large_aggregate_keeps_exact_int_and_exact_label(qtbot):
    report = income_expense_report(
        [operation(OperationKind.EXPENSE, MAX_MINOR) for _ in range(3)], PERIOD
    )
    _, chart = chart_dialog(qtbot, report)
    chart.canvas.draw()
    assert type(chart.plotted_rows[0].totals.expense_minor) is int
    assert chart.plotted_rows[0].totals.expense_minor == 3 * MAX_MINOR
    assert format_report_amount(3 * MAX_MINOR, RUB) in {v.get_text() for v in chart.axes.texts}


def test_category_name_is_plain_text_and_long_label_is_bounded(qtbot):
    _, chart = chart_dialog(qtbot)
    chart._names[FOOD] = "$\\frac{1}{0}$ <b>Только текст</b> " * 4
    chart.kind.setCurrentIndex(1)
    chart.canvas.draw()
    labels = chart.axes.get_yticklabels()
    assert all(not v.get_parse_math() and len(v.get_text()) <= 34 for v in labels)


def test_transfer_without_fee_has_no_chart_data(qtbot):
    from tests.unit.test_analytics import B

    report = income_expense_report(
        [operation(OperationKind.TRANSFER, 10000, destination_account_id=B)], PERIOD
    )
    _, chart = chart_dialog(qtbot, report)
    assert report.operation_count == 0 and not chart.plotted_rows


def test_negative_only_graph_keeps_zero_and_exact_refund(qtbot):
    expense = operation(OperationKind.EXPENSE, 10000, day=date(2026, 9, 1))
    refund = operation(
        OperationKind.REFUND, 5000, refund_of=expense.id, refund_of_revision_id=expense.revision_id
    )
    _, chart = chart_dialog(qtbot, income_expense_report([expense, refund], PERIOD))
    chart.canvas.draw()
    low, high = chart.axes.get_xlim()
    assert low < 0 <= high
    assert "−50,00" in {v.get_text() for v in chart.axes.texts}
    assert all(isinstance(v, MonthlyFlow) for v in chart.plotted_rows)


def test_real_matplotlib_hit_test_opens_category_sources(qtbot):
    from matplotlib.backend_bases import MouseEvent

    dialog, chart = chart_dialog(qtbot)
    chart.kind.setCurrentIndex(1)
    chart.canvas.draw()
    bar = next(b for b, target in chart._targets.items() if target[0] == FOOD)
    x, y = chart.axes.transData.transform((bar.get_width() / 2, bar.get_y() + bar.get_height() / 2))
    event = MouseEvent("button_press_event", chart.canvas, x, y, button=1)
    chart.figure.pick(event)
    assert dialog.tabs.currentWidget() is dialog.source_panel
    assert sum(v.totals.expense_minor for v in dialog._source_rows) == 15000


def test_small_amount_remains_exact_beside_aggregate_over_int64(qtbot):
    report = income_expense_report(
        [
            operation(OperationKind.EXPENSE, MAX_MINOR, category=OTHER),
            operation(OperationKind.EXPENSE, 1, category=FOOD),
        ],
        PERIOD,
    )
    _dialog, chart = chart_dialog(qtbot, report)
    chart.kind.setCurrentIndex(1)
    chart.canvas.draw()
    assert chart.coordinate_divisor > 1
    assert "0,01" in {v.get_text() for v in chart.axes.texts}
    assert next(v for v in chart.plotted_rows if v.category_id == FOOD).totals.expense_minor == 1


def test_pages_share_the_same_visual_scale(qtbot):
    values = [
        operation(OperationKind.EXPENSE, 10 ** (i + 1), category=str(uuid4())) for i in range(9)
    ]
    _dialog, chart = chart_dialog(qtbot, income_expense_report(values, PERIOD))
    chart.kind.setCurrentIndex(1)
    original = chart.axes.get_xlim()
    chart.next.click()
    assert chart.axes.get_xlim() == original
