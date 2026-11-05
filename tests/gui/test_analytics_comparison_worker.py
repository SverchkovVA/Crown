from PySide6.QtCore import QDate, QThread
from PySide6.QtWidgets import QDialog

from personal_finance.application.analytics import Analytics
from personal_finance.domain.models import CategoryKind
from personal_finance.presentation.analytics_comparison import (
    ComparisonPeriodDialog,
    IncomeExpenseComparisonDialog,
)
from personal_finance.presentation.main_window import MainWindow
from tests.gui.test_analytics_gui import PERIOD, fingerprints, populate


def test_filters_reach_one_snapshot_worker_and_both_actions_follow_busy_state(
    qtbot, session, monkeypatch
):
    populate(session)
    account = session.catalog.accounts()[0]
    category = next(c for c in session.catalog.categories() if c.kind == CategoryKind.EXPENSE)
    window = MainWindow()
    qtbot.addWidget(window)
    assert not window.compare_action.isEnabled()
    window.activate_session(session)
    assert window.compare_action.isEnabled()
    before = fingerprints(session)
    seen = []
    reads = []
    original = session.ledger.operations

    def operations(*args, **kwargs):
        reads.append(QThread.currentThread())
        return original(*args, **kwargs)

    monkeypatch.setattr(session.ledger, "operations", operations)

    def choose(dialog):
        dialog.start.setDate(QDate(2026, 9, 1))
        dialog.end.setDate(QDate(2026, 9, 30))
        dialog.second_start.setDate(QDate(2026, 10, 1))
        dialog.second_end.setDate(QDate(2026, 10, 31))
        dialog.account.setCurrentIndex(dialog.account.findData(account.id))
        dialog.category.setCurrentIndex(dialog.category.findData(category.id))
        dialog._accept()
        return QDialog.DialogCode.Accepted

    def show(dialog):
        assert QThread.currentThread() == window.thread() and not window._busy and not window._jobs
        seen.append(dialog.comparison)
        return QDialog.DialogCode.Rejected

    monkeypatch.setattr(ComparisonPeriodDialog, "exec", choose)
    monkeypatch.setattr(IncomeExpenseComparisonDialog, "exec", show)
    window.request_comparison()
    assert (
        window._busy
        and not window.compare_action.isEnabled()
        and not window.report_action.isEnabled()
    )
    qtbot.waitUntil(lambda: not window._busy and bool(seen), timeout=15000)
    result = seen[0]
    assert len(reads) == 1 and reads[0] != window.thread()
    assert (
        result.before.scope == result.after.scope
        and result.before.scope.account_ids == frozenset({account.id})
    )
    assert result.after.scope.category_ids == frozenset({category.id})
    assert result.totals[0].income_delta == 0 and result.totals[0].expense_delta == 230
    assert fingerprints(session) == before
    assert window.compare_action.isEnabled() and window.report_action.isEnabled()
    window.lock_book()
    assert not window.compare_action.isEnabled()


def test_cancel_does_not_start_comparison(qtbot, session, monkeypatch):
    window = MainWindow()
    qtbot.addWidget(window)
    window.activate_session(session)
    monkeypatch.setattr(ComparisonPeriodDialog, "exec", lambda dialog: QDialog.DialogCode.Rejected)
    window.request_comparison()
    assert not window._busy and not window._jobs
    window.lock_book()


def test_stale_comparison_callback_does_not_open_a_dialog(qtbot, session, monkeypatch):
    populate(session)
    comparison = Analytics(session.ledger, session.mutex, session.ensure_open).compare(
        PERIOD, PERIOD
    )
    window = MainWindow()
    qtbot.addWidget(window)
    window.activate_session(session)
    window.lock_book()
    seen = []
    monkeypatch.setattr(IncomeExpenseComparisonDialog, "exec", lambda dialog: seen.append(dialog))
    window._show_comparison(session, (comparison, [], []))
    assert not seen
