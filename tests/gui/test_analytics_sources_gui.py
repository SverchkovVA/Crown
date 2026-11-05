from datetime import date

from PySide6.QtWidgets import QDialog

from personal_finance.domain.models import Category, CategoryKind
from personal_finance.presentation.analytics import IncomeExpenseDialog, OperationSourceDialog
from tests.unit.test_analytics import FOOD, INCOME, OTHER
from tests.unit.test_analytics_sources import example


def dialog(qtbot):
    categories = [
        Category(FOOD, "Продукты", CategoryKind.EXPENSE),
        Category(OTHER, "Другие покупки", CategoryKind.EXPENSE),
        Category(INCOME, "Доход", CategoryKind.INCOME),
    ]
    value = IncomeExpenseDialog(example(), categories)
    qtbot.addWidget(value)
    return value


def test_category_source_rows_explain_split_and_exact_original_version(qtbot):
    value = dialog(qtbot)
    row = next(i for i, c in enumerate(value._category_order) if c.category_id == FOOD)
    value.tabs.setCurrentWidget(value.category_table)
    value.category_table.selectRow(row)
    value.source_button.click()
    assert (
        value.tabs.currentWidget() is value.source_panel
        and "Продукты" in value.source_context.text()
    )
    assert (
        value.source_table.model().rowCount() == 2
        and sum(r.totals.expense_minor for r in value._source_rows) == 15000
    )
    first = value._source_rows[0]
    assert first.operation.draft.amount.minor == 30000 and first.totals.expense_minor == 20000


def test_month_sources_keep_refund_as_negative_expense(qtbot):
    value = dialog(qtbot)
    row = next(i for i, v in enumerate(value.report.months) if v.month == date(2026, 11, 1))
    value.tabs.setCurrentWidget(value.month_table)
    value.month_table.selectRow(row)
    value.source_button.click()
    assert len(value._source_rows) == 1 and value._source_rows[0].totals.expense_minor == -5000
    assert "−50,00" in value.source_totals.text() and "11.2026" in value.source_context.text()


def test_reset_sources_keeps_currency_and_restores_complete_period(qtbot):
    value = dialog(qtbot)
    value._select_sources("RUB", FOOD)
    value.source_reset.click()
    assert len(value._source_rows) == 4 and value.source_currency.currentData() == "RUB"
    assert value._source_category is None and value._source_month is None


def test_details_show_full_amount_and_only_selected_contribution_without_database(
    qtbot, monkeypatch
):
    value = dialog(qtbot)
    value._select_sources("RUB", FOOD)
    seen = []

    def show(details):
        seen.append(
            (details.fields["Сумма операции"].text(), details.fields["Расход в отборе"].text())
        )
        return QDialog.DialogCode.Rejected

    monkeypatch.setattr(OperationSourceDialog, "exec", show)
    value.source_details.click()
    assert seen == [("300,00 RUB", "200,00 RUB")]


def test_repeated_filter_changes_do_not_connect_duplicate_detail_handlers(qtbot, monkeypatch):
    value = dialog(qtbot)
    value._select_sources("RUB")
    seen = []
    monkeypatch.setattr(OperationSourceDialog, "exec", lambda details: seen.append(details.flow))
    for _ in range(4):
        value._clear_source_filter()
    value.source_table.doubleClicked.emit(value.source_table.model().index(0, 0))
    assert len(seen) == 1


def test_empty_report_has_no_enabled_source_actions(qtbot):
    from personal_finance.domain.analytics import income_expense_report
    from tests.unit.test_analytics import PERIOD

    value = IncomeExpenseDialog(income_expense_report([], PERIOD), [])
    qtbot.addWidget(value)
    assert not value.source_button.isEnabled() and not value.source_details.isEnabled()
    value.tabs.setCurrentWidget(value.source_panel)
    assert not value.source_details.isEnabled() and not value.source_reset.isEnabled()
