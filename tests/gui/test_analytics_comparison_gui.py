from dataclasses import replace
from datetime import date

import pytest
from PySide6.QtCore import QDate, Qt
from PySide6.QtWidgets import QDialog

from personal_finance.domain.analytics import ReportPeriod, ReportScope
from personal_finance.domain.analytics_comparison import compare_periods
from personal_finance.domain.ledger import OperationKind
from personal_finance.domain.models import (
    Account,
    AccountKind,
    Category,
    CategoryKind,
    Currency,
    Money,
)
from personal_finance.presentation.analytics import IncomeExpenseDialog
from personal_finance.presentation.analytics_comparison import (
    ComparisonPeriodDialog,
    IncomeExpenseComparisonDialog,
)
from tests.unit.test_analytics import FOOD, OTHER, operation
from tests.unit.test_analytics import A as ACCOUNT
from tests.unit.test_analytics_comparison import FIRST, SECOND, flows
from tests.unit.test_analytics_sources import example


def catalogs():
    income = operation(OperationKind.INCOME, 100).draft.category_id
    return [
        Category(FOOD, "Вымышленные продукты", CategoryKind.EXPENSE),
        Category(OTHER, "Вымышленное прочее", CategoryKind.EXPENSE),
        Category(income, "Вымышленный доход", CategoryKind.INCOME),
    ], [Account(ACCOUNT, "Вымышленный счёт", AccountKind.CASH, None)]


def form(qtbot):
    categories, accounts = catalogs()
    dialog = ComparisonPeriodDialog(accounts=accounts, categories=categories)
    qtbot.addWidget(dialog)
    dialog.start.setDate(QDate(2026, 9, 1))
    dialog.end.setDate(QDate(2026, 9, 30))
    dialog.second_start.setDate(QDate(2026, 10, 1))
    dialog.second_end.setDate(QDate(2026, 10, 31))
    return dialog


def view(qtbot, result=None):
    categories, accounts = catalogs()
    dialog = IncomeExpenseComparisonDialog(
        result or compare_periods(flows(), FIRST, SECOND), categories, accounts=accounts
    )
    qtbot.addWidget(dialog)
    return dialog


def test_form_returns_two_periods_and_one_shared_scope(qtbot):
    dialog = form(qtbot)
    dialog.account.setCurrentIndex(dialog.account.findData(ACCOUNT))
    dialog.category.setCurrentIndex(dialog.category.findData(FOOD))
    dialog._accept()
    assert dialog.result_period == FIRST and dialog.result_second_period == SECOND
    assert dialog.result_scope == ReportScope(frozenset({ACCOUNT}), frozenset({FOOD}))
    assert dialog.start.displayFormat() == dialog.second_start.displayFormat() == "dd.MM.yyyy"


@pytest.mark.parametrize("second", [False, True])
def test_invalid_period_keeps_edits_and_clears_ready_results(qtbot, second):
    dialog = form(qtbot)
    dialog.category.setCurrentIndex(dialog.category.findData(FOOD))
    dialog._accept()
    (dialog.second_start if second else dialog.start).setDate(QDate(2026, 12, 31))
    dialog._accept()
    assert dialog.result_period is dialog.result_second_period is dialog.result_scope is None
    assert ("Второй" if second else "Первый") in dialog.feedback.text()
    assert dialog.category.currentData() == FOOD
    assert (dialog.second_start if second else dialog.start).date() == QDate(2026, 12, 31)


def test_unequal_and_equal_periods_are_explicit_and_allowed(qtbot):
    dialog = form(qtbot)
    dialog.second_end.setDate(QDate(2026, 10, 2))
    assert "30 и 2" in dialog.note.text() and "не пересчитываются на день" in dialog.note.text()
    dialog._accept()
    assert dialog.result_second_period == ReportPeriod(date(2026, 10, 1), date(2026, 10, 2))
    dialog.second_start.setDate(dialog.start.date())
    dialog.second_end.setDate(dialog.end.date())
    dialog._accept()
    assert dialog.result_period == dialog.result_second_period == FIRST


def test_delta_cells_are_exact_and_tables_are_readonly(qtbot):
    dialog = view(qtbot)
    model = dialog.total_table.model()
    assert model.rowCount() == 3 and [model.data(model.index(i, 4)) for i in range(3)] == [
        "+500,00",
        "−100,00",
        "+600,00",
    ]
    assert not model.flags(model.index(0, 2)) & Qt.ItemFlag.ItemIsEditable
    assert dialog.scope_context.textFormat() == Qt.TextFormat.PlainText


def test_empty_comparison_does_not_offer_sources(qtbot, monkeypatch):
    dialog = view(qtbot, compare_periods([], FIRST, SECOND))
    seen = []
    monkeypatch.setattr(IncomeExpenseDialog, "exec", lambda child: seen.append(child))
    assert not dialog.before_button.isEnabled() and not dialog.after_button.isEnabled()
    assert "нет" in dialog.notice.text()
    dialog.open_period(True)
    assert not seen


def test_missing_currency_side_never_opens_another_currency(qtbot, monkeypatch):
    usd = Currency("USD", 2)
    result = compare_periods(
        [
            operation(OperationKind.INCOME, 200, day=date(2026, 9, 2)),
            operation(OperationKind.INCOME, 500, currency=usd, fee=Money(0, usd)),
        ],
        FIRST,
        SECOND,
    )
    dialog = view(qtbot, result)
    seen = []

    def show(child):
        seen.append((child.report, child.source_currency.currentData(), child._source_rows))
        return QDialog.DialogCode.Rejected

    monkeypatch.setattr(IncomeExpenseDialog, "exec", show)
    assert dialog.before_button.isEnabled() and not dialog.after_button.isEnabled()
    dialog.total_table.selectRow(3)
    assert not dialog.before_button.isEnabled() and dialog.after_button.isEnabled()
    dialog.open_period(False)
    assert not seen
    dialog.open_period(True)
    assert len(seen) == 1 and seen[0][0] is result.after and seen[0][1] == "USD"
    assert all(v.operation.draft.amount.currency == usd for v in seen[0][2])


def test_category_drilldown_uses_same_scoped_reports_and_reset_keeps_parts(qtbot, monkeypatch):
    scope = ReportScope(frozenset({ACCOUNT}), frozenset({FOOD}))
    result = compare_periods(
        example().sources, SECOND, ReportPeriod(date(2026, 11, 1), date(2026, 11, 30)), scope
    )
    dialog = view(qtbot, result)
    dialog.tabs.setCurrentWidget(dialog.category_table)
    seen = []

    def show(child):
        child.source_reset.click()
        seen.append((child.report, child._source_rows))
        return QDialog.DialogCode.Rejected

    monkeypatch.setattr(IncomeExpenseDialog, "exec", show)
    dialog.open_period(False)
    dialog.open_period(True)
    assert seen[0][0] is result.before and seen[1][0] is result.after
    assert seen[0][0].scope == seen[1][0].scope == scope
    assert (
        seen[0][1][0].operation.draft.amount.minor == 30000
        and seen[0][1][0].totals.expense_minor == 20000
    )
    assert seen[1][1][0].totals.expense_minor == -5000


def test_category_missing_on_one_side_is_not_replaced_by_other_categories(qtbot, monkeypatch):
    result = compare_periods(
        [
            operation(OperationKind.INCOME, 100, day=date(2026, 9, 2)),
            operation(OperationKind.EXPENSE, 200),
        ],
        FIRST,
        SECOND,
    )
    dialog = view(qtbot, result)
    dialog.tabs.setCurrentWidget(dialog.category_table)
    row = next(i for i, target in enumerate(dialog._category_targets) if target[1] == FOOD)
    dialog.category_table.selectRow(row)
    assert not dialog.before_button.isEnabled() and dialog.after_button.isEnabled()
    seen = []
    monkeypatch.setattr(
        IncomeExpenseDialog,
        "exec",
        lambda child: seen.append(child.report) or QDialog.DialogCode.Rejected,
    )
    dialog.open_period(False)
    assert not seen
    dialog._open_available(dialog.category_table.currentIndex())
    assert seen == [result.after]


def test_category_names_are_plain_and_scope_tooltip_is_escaped(qtbot):
    result = compare_periods(
        example().sources, SECOND, SECOND, ReportScope(category_ids=frozenset({FOOD}))
    )
    categories, accounts = catalogs()
    categories[0] = replace(categories[0], name="<b>Вымышленная</b>")
    dialog = IncomeExpenseComparisonDialog(result, categories, accounts=accounts)
    qtbot.addWidget(dialog)
    assert "<b>" in dialog.scope_context.text() and "&lt;b&gt;" in dialog.scope_context.toolTip()
    assert (
        dialog.category_table.model().data(dialog.category_table.model().index(0, 0))
        == "<b>Вымышленная</b>"
    )
