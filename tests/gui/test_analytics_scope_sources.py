from dataclasses import replace

from PySide6.QtCore import Qt

from personal_finance.domain.analytics import ReportScope, income_expense_report
from personal_finance.domain.models import Account, AccountKind, Category, CategoryKind
from personal_finance.presentation.analytics import IncomeExpenseDialog, scope_captions
from tests.unit.test_analytics import FOOD, OTHER, PERIOD, A
from tests.unit.test_analytics_sources import example


def scoped(scope=None):
    return income_expense_report(example().sources, PERIOD, scope)


def catalogs():
    return [
        Category(FOOD, "Продукты", CategoryKind.EXPENSE),
        Category(OTHER, "Прочее", CategoryKind.EXPENSE),
    ], [Account(A, "Вымышленный счёт", AccountKind.CASH, None)]


def test_global_filter_survives_local_selection_and_reset(qtbot, monkeypatch):
    report = scoped(ReportScope(category_ids=frozenset({FOOD})))
    categories, accounts = catalogs()
    dialog = IncomeExpenseDialog(report, categories, accounts=accounts)
    qtbot.addWidget(dialog)
    assert "Категории: Продукты" in dialog.scope_context.text()
    assert dialog.scope_context.textFormat() == Qt.TextFormat.PlainText
    assert dialog.source_reset.text() == "Все операции отчёта"
    dialog.charts.sources_requested.emit("RUB", FOOD, None)
    assert (
        len(dialog._source_rows) == 2
        and sum(r.totals.expense_minor for r in dialog._source_rows) == 15000
    )
    assert any(r.operation.draft.amount.minor == 30000 for r in dialog._source_rows)
    dialog.source_reset.click()
    assert (
        len(dialog._source_rows) == 2
        and sum(r.totals.expense_minor for r in dialog._source_rows) == 15000
    )
    assert dialog.report is report and dialog.report.scope.category_ids == frozenset({FOOD})
    assert dialog.charts.report is report
    dialog.reject()


def test_empty_scope_remains_empty_in_graphs_and_sources(qtbot, monkeypatch):
    categories, accounts = catalogs()
    dialog = IncomeExpenseDialog(
        scoped(ReportScope(account_ids=frozenset())), categories, accounts=accounts
    )
    qtbot.addWidget(dialog)
    assert "Счета: не выбраны" in dialog.scope_context.text()
    assert not dialog._source_rows and not dialog.report.totals
    dialog.source_reset.click()
    assert not dialog._source_rows
    dialog.reject()


def test_scope_names_are_plain_and_tooltip_escaped(qtbot, monkeypatch):
    categories, accounts = catalogs()
    categories[0] = replace(categories[0], name="<b>Вымышленная</b>")
    dialog = IncomeExpenseDialog(
        scoped(ReportScope(category_ids=frozenset({FOOD}))), categories, accounts=accounts
    )
    qtbot.addWidget(dialog)
    assert (
        "<b>" in dialog.scope_context.text()
        and dialog.scope_context.textFormat() == Qt.TextFormat.PlainText
    )
    assert "&lt;b&gt;" in dialog.scope_context.toolTip()
    dialog.reject()


def test_long_scope_is_compact_but_full_names_remain_in_tooltip():
    categories, accounts = catalogs()
    accounts[0] = replace(accounts[0], name="Длинный вымышленный счёт " * 10)
    brief, full = scope_captions(
        scoped(ReportScope(account_ids=frozenset({A}))), categories, accounts
    )
    assert "выбрано: 1" in brief and accounts[0].name in full
