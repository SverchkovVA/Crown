from datetime import date

from PySide6.QtCore import QDate, QThread
from PySide6.QtWidgets import QDialogButtonBox

from personal_finance.application.analytics import Analytics
from personal_finance.domain.ledger import OperationDraft, OperationKind, OperationPart
from personal_finance.domain.models import AccountKind, CategoryKind, Money
from personal_finance.presentation.analytics import OperationSourceDialog
from personal_finance.presentation.budget import MonthlyBudgetDialog, MonthlyBudgetOptions
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
    method = Analytics.monthly_budget
    worker = []

    def observe(service, *args):
        worker.append(QThread.currentThread() != window.thread())
        return method(service, *args)

    monkeypatch.setattr(Analytics, "monthly_budget", observe)
    window = MainWindow()
    qtbot.addWidget(window)
    window.show()
    assert not window.budget_action.isEnabled()
    window.activate_session(session)
    window.navigation.setCurrentRow(2)
    button = next(b for b in window.report_buttons if b.text() == "Месячный бюджет…")
    assert button.isEnabled()
    button.click()
    panel = window._report_forms

    def current(kind):
        dialog = panel.stack.currentWidget().widget()
        assert isinstance(dialog, kind) and dialog.isVisible() and not dialog.isWindow()
        return dialog

    options = current(MonthlyBudgetOptions)
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

    result = current(MonthlyBudgetDialog)
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
    assert current(MonthlyBudgetDialog) is result
    result.findChild(QDialogButtonBox).button(QDialogButtonBox.StandardButton.Close).click()
    assert panel.stack.count() == 0 and button.isVisible()
    assert fingerprints(session) == before
    window.lock_book()
