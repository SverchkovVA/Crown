from PySide6.QtCore import QDate, QThread
from PySide6.QtWidgets import QDialog

from personal_finance.domain.models import CategoryKind
from personal_finance.presentation.analytics import IncomeExpenseDialog, ReportPeriodDialog
from personal_finance.presentation.main_window import MainWindow
from tests.conftest import session as session
from tests.gui.test_analytics_gui import fingerprints, populate


def test_selected_account_category_reach_readonly_worker(qtbot, session, monkeypatch):
    populate(session)
    account = session.catalog.accounts()[0]
    category = next(c for c in session.catalog.categories() if c.kind == CategoryKind.EXPENSE)
    window = MainWindow()
    qtbot.addWidget(window)
    window.activate_session(session)
    before = fingerprints(session)
    seen = []

    def choose(dialog):
        dialog.start.setDate(QDate(2026, 10, 1))
        dialog.end.setDate(QDate(2026, 10, 31))
        dialog.account.setCurrentIndex(dialog.account.findData(account.id))
        dialog.category.setCurrentIndex(dialog.category.findData(category.id))
        dialog._accept()
        return QDialog.DialogCode.Accepted

    def show(dialog):
        assert QThread.currentThread() == window.thread() and not window._busy and not window._jobs
        seen.append(dialog.report)
        assert "Категории:" in dialog.scope_context.text()
        return QDialog.DialogCode.Rejected

    monkeypatch.setattr(ReportPeriodDialog, "exec", choose)
    monkeypatch.setattr(IncomeExpenseDialog, "exec", show)
    window.request_analytics()
    qtbot.waitUntil(lambda: not window._busy and bool(seen), timeout=15000)
    assert seen[0].scope.account_ids == frozenset({account.id})
    assert seen[0].scope.category_ids == frozenset({category.id})
    assert (
        seen[0].operation_count == 1
        and seen[0].totals[0].income_minor == 0
        and seen[0].totals[0].expense_minor == 230
    )
    assert fingerprints(session) == before
    window.lock_book()


def test_bank_without_accounts_cannot_expand_report_to_every_account(qtbot, session, monkeypatch):
    populate(session)
    bank = session.catalog.add_institution("Вымышленный пустой банк")
    window = MainWindow()
    qtbot.addWidget(window)
    window.activate_session(session)
    before = fingerprints(session)
    seen = []

    def choose(dialog):
        dialog.start.setDate(QDate(2026, 10, 1))
        dialog.end.setDate(QDate(2026, 10, 31))
        dialog.bank.setCurrentIndex(dialog.bank.findData(bank.id))
        dialog._accept()
        return QDialog.DialogCode.Accepted

    monkeypatch.setattr(ReportPeriodDialog, "exec", choose)
    monkeypatch.setattr(
        IncomeExpenseDialog,
        "exec",
        lambda dialog: seen.append(dialog.report) or QDialog.DialogCode.Rejected,
    )
    window.request_analytics()
    qtbot.waitUntil(lambda: not window._busy and bool(seen), timeout=15000)
    assert seen[0].scope.account_ids == frozenset() and seen[0].sources == seen[0].totals == ()
    assert fingerprints(session) == before
    window.lock_book()
