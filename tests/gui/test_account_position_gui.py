from datetime import date

from PySide6.QtCore import QDate, QThread

from personal_finance.application.analytics import Analytics
from personal_finance.domain.ledger import OperationDraft, OperationKind
from personal_finance.domain.models import AccountKind, CategoryKind, Money
from personal_finance.presentation.account_position import (
    AccountPositionDialog,
    AccountPositionOptions,
)
from personal_finance.presentation.analytics import OperationSourceDialog
from personal_finance.presentation.analytics_cashflow import CashFlowSourcesDialog
from personal_finance.presentation.main_window import MainWindow
from tests.gui.test_analytics_gui import fingerprints


def test_account_position_is_embedded_read_only_and_reaches_original_operation(
    qtbot, session, monkeypatch
):
    bank = session.catalog.add_account("Банковский проверочный", AccountKind.DEBIT)
    investment = session.catalog.add_account("Инвестиционный проверочный", AccountKind.INVESTMENT)
    empty = session.catalog.add_account("Пустой проверочный", AccountKind.CASH)
    expense = session.catalog.add_category("Проверочный расход", CategoryKind.EXPENSE)
    opening = session.ledger.add(
        OperationDraft(OperationKind.OPENING, date(2026, 10, 1), bank.id, Money(100000))
    )
    transfer = session.ledger.add(
        OperationDraft(
            OperationKind.TRANSFER,
            date(2026, 10, 2),
            bank.id,
            Money(30000),
            destination_account_id=investment.id,
            fee=Money(100),
            fee_category_id=expense.id,
        )
    )
    session.ledger.add(
        OperationDraft(OperationKind.EXPENSE, date(2026, 10, 3), bank.id, Money(20000), expense.id)
    )
    before = fingerprints(session)
    method = Analytics.account_position
    worker = []

    def observe(service, *args):
        worker.append(QThread.currentThread() != window.thread())
        return method(service, *args)

    monkeypatch.setattr(Analytics, "account_position", observe)
    window = MainWindow()
    qtbot.addWidget(window)
    window.show()
    assert not window.account_position_action.isEnabled()
    window.activate_session(session)
    window.navigation.setCurrentRow(2)
    button = next(b for b in window.report_buttons if b.text() == "Средства на счетах…")
    button.click()
    panel = window._report_forms

    def current(kind):
        form = panel.stack.currentWidget().widget()
        assert isinstance(form, kind) and form.isVisible() and not form.isWindow()
        return form

    options = current(AccountPositionOptions)
    options.as_of.setDate(QDate(2026, 10, 2))
    options.calculate.click()
    qtbot.waitUntil(lambda: not window._busy, timeout=15000)
    result = current(AccountPositionDialog)
    assert worker == [True]
    assert {row.account.id: row.minor for row in result.report.accounts} == {
        bank.id: 69900,
        investment.id: 30000,
        empty.id: 0,
    }
    assert result.totals.model().index(0, 3).data() == "999,00"
    index = next(i for i, row in enumerate(result.report.accounts) if row.account.id == empty.id)
    result.accounts.selectRow(index)
    assert not result.open_sources.isEnabled()
    index = next(i for i, row in enumerate(result.report.accounts) if row.account.id == bank.id)
    result.accounts.selectRow(index)
    result.open_sources.click()
    sources = current(CashFlowSourcesDialog)
    assert [line.operation.revision_id for line in sources.lines] == [
        opening.revision_id,
        transfer.revision_id,
    ]
    assert sum(line.minor for line in sources.lines) == 69900
    sources.table.selectRow(1)
    sources.details_button.click()
    details = current(OperationSourceDialog)
    assert details.fields["Сумма операции"].text() == "300,00 RUB"
    details.reject()
    assert current(CashFlowSourcesDialog) is sources
    sources.reject()
    assert current(AccountPositionDialog) is result
    result.reject()
    assert current(AccountPositionOptions) is options and options.as_of.date() == QDate(2026, 10, 2)
    options.as_of.setDate(QDate(2026, 10, 3))
    options.calculate.click()
    qtbot.waitUntil(lambda: not window._busy, timeout=15000)
    second = current(AccountPositionDialog)
    assert (
        second.report.totals[0].total_minor == 79900
        and result.report.totals[0].total_minor == 99900
    )
    second.reject()
    options.reject()
    assert fingerprints(session) == before
    window.lock_book()
    assert not window.account_position_action.isEnabled()
