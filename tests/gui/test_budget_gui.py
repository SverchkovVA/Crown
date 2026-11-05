from datetime import date

import pytest
from PySide6.QtCore import QDate, QThread
from PySide6.QtWidgets import QDialogButtonBox

from personal_finance.application.analytics import Analytics
from personal_finance.domain.analytics import ReportPeriod
from personal_finance.domain.errors import ValidationError
from personal_finance.domain.ledger import OperationDraft, OperationKind, OperationPart
from personal_finance.domain.models import AccountKind, CategoryKind, Money
from personal_finance.presentation.analytics import OperationSourceDialog
from personal_finance.presentation.budget import BudgetDialog, BudgetOptions
from personal_finance.presentation.main_window import MainWindow
from tests.gui.test_analytics_gui import fingerprints


def test_monthly_budget_reports_net_scoped_expense_and_opens_source(qtbot, session, monkeypatch):
    account = session.catalog.add_account("Проверочный счёт", AccountKind.CASH)
    other_account = session.catalog.add_account("Второй счёт", AccountKind.CASH)
    food = session.catalog.add_category("Еда для проверки", CategoryKind.EXPENSE)
    other = session.catalog.add_category("Другие расходы", CategoryKind.EXPENSE)
    income = session.catalog.add_category("Проверочный доход", CategoryKind.INCOME)
    purchase = session.ledger.add(
        OperationDraft(
            OperationKind.EXPENSE,
            date(2026, 10, 5),
            account.id,
            Money(130000),
            parts=(OperationPart(food.id, Money(120000)), OperationPart(other.id, Money(10000))),
        )
    )
    refund = session.ledger.add(
        OperationDraft(
            OperationKind.REFUND,
            date(2026, 10, 6),
            account.id,
            Money(25000),
            food.id,
            refund_of=purchase.id,
            refund_of_revision_id=purchase.revision_id,
        )
    )
    session.ledger.add(
        OperationDraft(
            OperationKind.EXPENSE,
            date(2026, 10, 8),
            other_account.id,
            Money(40000),
            food.id,
        )
    )
    before = fingerprints(session)
    method = Analytics.budget
    worker = []

    def observe(service, *args):
        worker.append(QThread.currentThread() != window.thread())
        return method(service, *args)

    monkeypatch.setattr(Analytics, "budget", observe)
    window = MainWindow()
    qtbot.addWidget(window)
    window.show()
    assert not window.budget_action.isEnabled()
    window.activate_session(session)
    window.navigation.setCurrentRow(2)
    button = next(b for b in window.report_buttons if b.text() == "Бюджет расходов…")
    assert button.isEnabled()
    button.click()
    panel = window._report_forms

    def current(kind):
        dialog = panel.stack.currentWidget().widget()
        assert isinstance(dialog, kind) and dialog.isVisible() and not dialog.isWindow()
        return dialog

    options = current(BudgetOptions)
    assert options.category.findData(income.id) == -1
    options.limit.setText("-0,01")
    options.calculate.click()
    assert options.feedback.isVisible() and options.result_request is None
    options.month.setDate(QDate(2026, 10, 15))
    options.limit.setText("1 500,00")
    options.account.setCurrentIndex(options.account.findData(account.id))
    options.category.setCurrentIndex(options.category.findData(food.id))
    options.calculate.click()
    qtbot.waitUntil(lambda: not window._busy, timeout=15000)

    result = current(BudgetDialog)
    assert worker == [True]
    assert (result.report.spent_minor, result.report.remaining_minor) == (95000, 55000)
    assert [row.operation.revision_id for row in result.report.sources] == [
        purchase.revision_id,
        refund.revision_id,
    ]
    assert result.sources.model().index(1, 2).data() == "−250,00 RUB"
    result.sources.selectRow(0)
    result.open_source.click()
    details = current(OperationSourceDialog)
    assert details.fields["Сумма операции"].text() == "1 300,00 RUB"
    assert details.fields["Расход в отборе"].text() == "1 200,00 RUB"
    details.reject()
    assert current(BudgetDialog) is result
    result.findChild(QDialogButtonBox).button(QDialogButtonBox.StandardButton.Close).click()
    assert current(BudgetOptions) is options
    assert options.limit.text() == "1 500,00"
    assert options.account.currentData() == account.id
    assert options.category.currentData() == food.id
    options.limit.setText("2 000")
    options.calculate.click()
    qtbot.waitUntil(lambda: not window._busy, timeout=15000)
    second = current(BudgetDialog)
    assert worker == [True, True]
    assert second.report.remaining_minor == 105000
    assert result.report.remaining_minor == 55000
    second.reject()
    assert current(BudgetOptions) is options
    options.reject()
    assert panel.stack.count() == 0 and button.isVisible()
    assert fingerprints(session) == before
    window.lock_book()


def test_budget_custom_dates_allow_retry_after_error_without_losing_parameters(
    qtbot, session, monkeypatch
):
    account = session.catalog.add_account("Проверка периода", AccountKind.CASH)
    category = session.catalog.add_category("Проверка расходов", CategoryKind.EXPENSE)
    for day, amount in [
        (date(2026, 10, 31), 10000),
        (date(2026, 11, 1), 20000),
        (date(2026, 11, 2), 50000),
    ]:
        session.ledger.add(
            OperationDraft(OperationKind.EXPENSE, day, account.id, Money(amount), category.id)
        )
    before = fingerprints(session)
    method = Analytics.budget
    calls = []

    def calculate(service, *args):
        calls.append(args)
        if len(calls) == 1:
            raise ValidationError("Проверочная ошибка расчёта")
        return method(service, *args)

    monkeypatch.setattr(Analytics, "budget", calculate)
    window = MainWindow()
    qtbot.addWidget(window)
    window.show()
    window.activate_session(session)
    window.navigation.setCurrentRow(2)
    window.budget_action.trigger()
    panel = window._report_forms
    options = panel.stack.currentWidget().widget()
    assert isinstance(options, BudgetOptions)
    options.period_mode.setCurrentIndex(1)
    assert options.start.isVisible() and options.end.isVisible() and not options.month.isVisible()
    options.start.setDate(QDate(2026, 10, 31))
    options.end.setDate(QDate(2026, 11, 1))
    options.limit.setText("500")
    options.calculate.click()
    qtbot.waitUntil(lambda: not window._busy, timeout=15000)
    assert panel.stack.currentWidget().widget() is options and options.isEnabled()
    assert panel.notice.text() == "Проверочная ошибка расчёта"
    assert options.limit.text() == "500"
    options.end.setDate(QDate(2026, 10, 30))
    options.calculate.click()
    assert options.feedback.isVisible() and "начало не позже окончания" in options.feedback.text()
    assert options.result_request is None and len(calls) == 1
    options.end.setDate(QDate(2026, 11, 1))
    options.calculate.click()
    qtbot.waitUntil(lambda: not window._busy, timeout=15000)
    report = panel.stack.currentWidget().widget()
    assert isinstance(report, BudgetDialog)
    assert not options.feedback.isVisible() and not panel.notice.isVisible()
    assert report.report.period == ReportPeriod(date(2026, 10, 31), date(2026, 11, 1))
    assert (report.report.spent_minor, report.report.remaining_minor) == (30000, 20000)
    report.reject()
    assert panel.stack.currentWidget().widget() is options
    assert options.start.date() == QDate(2026, 10, 31) and options.end.date() == QDate(2026, 11, 1)
    options.reject()
    assert panel.stack.count() == 0
    assert fingerprints(session) == before
    window.lock_book()


@pytest.mark.parametrize("day", [date(1, 1, 1), date(9999, 12, 31)])
def test_budget_options_accept_full_supported_date_range(qtbot, day):
    options = BudgetOptions([], [])
    qtbot.addWidget(options)
    options.show()
    options.limit.setText("0")
    options.month.setDate(QDate(day.year, day.month, day.day))
    with qtbot.waitSignal(options.calculationRequested):
        options.calculate.click()
    period = options.result_request[0]
    assert period.start == day.replace(day=1) and period.end.month == day.month
    options.period_mode.setCurrentIndex(1)
    options.start.setDate(QDate(day.year, day.month, day.day))
    options.end.setDate(QDate(day.year, day.month, day.day))
    with qtbot.waitSignal(options.calculationRequested):
        options.calculate.click()
    assert options.result_request[0] == ReportPeriod(day, day)
    options.reject()
