from PySide6.QtCore import QDate, QThread
from PySide6.QtWidgets import QDialog

from personal_finance.application.analytics import Analytics
from personal_finance.presentation.analytics_cashflow import CashFlowDialog, CashFlowPeriodDialog
from personal_finance.presentation.main_window import MainWindow
from tests.gui.test_analytics_gui import PERIOD, fingerprints, populate


def test_cash_flow_worker_reads_once_keeps_scope_and_releases_ui_before_dialog(
    qtbot, session, monkeypatch
):
    populate(session)
    account = session.catalog.accounts()[0]
    window = MainWindow()
    qtbot.addWidget(window)
    assert not window.cash_flow_action.isEnabled()
    window.activate_session(session)
    assert window.cash_flow_action.isEnabled()
    before = fingerprints(session)
    reads = []
    seen = []
    original = session.ledger.operations

    def operations(*args, **kwargs):
        reads.append(QThread.currentThread())
        return original(*args, **kwargs)

    monkeypatch.setattr(session.ledger, "operations", operations)

    def choose(dialog):
        dialog.start.setDate(QDate(2026, 10, 1))
        dialog.end.setDate(QDate(2026, 10, 31))
        dialog.account.setCurrentIndex(dialog.account.findData(account.id))
        dialog._accept()
        return QDialog.DialogCode.Accepted

    def show(dialog):
        assert QThread.currentThread() == window.thread() and not window._busy and not window._jobs
        seen.append(dialog.report)
        return QDialog.DialogCode.Rejected

    monkeypatch.setattr(CashFlowPeriodDialog, "exec", choose)
    monkeypatch.setattr(CashFlowDialog, "exec", show)
    window.request_cash_flow()
    assert window._busy and not any(
        v.isEnabled()
        for v in [window.cash_flow_action, window.report_action, window.compare_action]
    )
    qtbot.waitUntil(lambda: not window._busy and bool(seen), timeout=15000)
    assert len(reads) == 1 and reads[0] != window.thread()
    assert (
        seen[0].scope.account_ids == frozenset({account.id})
        and seen[0].totals[0].closing_minor == 1020
    )
    assert fingerprints(session) == before
    window.lock_book()
    assert not window.cash_flow_action.isEnabled()


def test_cancelled_cash_flow_starts_no_job(qtbot, session, monkeypatch):
    window = MainWindow()
    qtbot.addWidget(window)
    window.activate_session(session)
    monkeypatch.setattr(CashFlowPeriodDialog, "exec", lambda dialog: QDialog.DialogCode.Rejected)
    window.request_cash_flow()
    assert not window._busy and not window._jobs
    window.lock_book()


def test_stale_cash_flow_callback_opens_no_dialog(qtbot, session, monkeypatch):
    populate(session)
    report = Analytics(session.ledger, session.mutex, session.ensure_open).cash_flow(PERIOD)
    window = MainWindow()
    qtbot.addWidget(window)
    window.activate_session(session)
    window.lock_book()
    seen = []
    monkeypatch.setattr(CashFlowDialog, "exec", lambda dialog: seen.append(dialog))
    window._show_cash_flow(session, (report, [], []))
    assert not seen
