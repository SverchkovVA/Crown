import hashlib
from datetime import date

from PySide6.QtCore import QDate, QThread
from PySide6.QtWidgets import QDialog

from personal_finance.application.analytics import Analytics
from personal_finance.domain.analytics import ReportPeriod, income_expense_report
from personal_finance.domain.ledger import OperationDraft, OperationKind
from personal_finance.domain.models import (
    MAX_MINOR,
    RUB,
    AccountKind,
    CategoryKind,
    Currency,
    Money,
)
from personal_finance.presentation.analytics import (
    IncomeExpenseDialog,
    ReportPeriodDialog,
    format_report_amount,
)
from personal_finance.presentation.main_window import MainWindow

PERIOD = ReportPeriod(date(2026, 10, 1), date(2026, 10, 31))


def fingerprints(session):
    return tuple(
        hashlib.sha256((session.root / "data" / name).read_bytes()).hexdigest()
        for name in ("ledger.sqlite3", "key.json")
    )


def populate(session):
    account = session.catalog.add_account("Вымышленный отчётный счёт", AccountKind.CASH)
    categories = {
        kind: next(c.id for c in session.catalog.categories() if c.kind == kind)
        for kind in CategoryKind
    }
    for kind, minor in [(OperationKind.INCOME, 1250), (OperationKind.EXPENSE, 230)]:
        session.ledger.add(
            OperationDraft(
                kind,
                date(2026, 10, 2),
                account.id,
                Money(minor),
                categories[
                    CategoryKind.INCOME if kind == OperationKind.INCOME else CategoryKind.EXPENSE
                ],
            )
        )


def test_period_dialog_explains_error_and_keeps_values(qtbot):
    dialog = ReportPeriodDialog()
    qtbot.addWidget(dialog)
    dialog.start.setDate(QDate(2026, 10, 31))
    dialog.end.setDate(QDate(2026, 10, 1))
    dialog._accept()
    assert dialog.result_period is None and "начало" in dialog.feedback.text()
    assert dialog.start.date() == QDate(2026, 10, 31)
    dialog.end.setDate(QDate(2026, 11, 1))
    dialog._accept()
    assert dialog.result_period == ReportPeriod(date(2026, 10, 31), date(2026, 11, 1))


def test_formatting_report_totals_does_not_overflow_or_use_float():
    assert format_report_amount(2 * MAX_MINOR, RUB) == "184 467 440 737 095 516,14"
    assert format_report_amount(-123, RUB) == "−1,23"
    assert format_report_amount(123, Currency("JPY", 0)) == "123"
    assert format_report_amount(123, Currency("XTS", 8)) == "0,00000123"


def test_empty_report_is_explicit_and_tables_are_readonly(qtbot):
    dialog = IncomeExpenseDialog(income_expense_report([], PERIOD), [])
    qtbot.addWidget(dialog)
    assert (
        "нет действующих" in dialog.notice.text() and dialog.summary_table.model().rowCount() == 0
    )


def test_report_worker_is_readonly_and_released_before_showing(qtbot, session, monkeypatch):
    populate(session)
    window = MainWindow()
    qtbot.addWidget(window)
    window.activate_session(session)
    before = fingerprints(session)
    seen = []

    def show(dialog):
        assert QThread.currentThread() == window.thread()
        assert not window._busy and not window._jobs
        seen.append(dialog.report)
        return QDialog.DialogCode.Rejected

    monkeypatch.setattr(IncomeExpenseDialog, "exec", show)
    window.build_analytics(PERIOD)
    assert window._busy and not window.report_action.isEnabled()
    qtbot.waitUntil(lambda: not window._busy and bool(seen), timeout=15000)
    assert seen[0].totals[0].difference_minor == 1020
    assert fingerprints(session) == before
    assert window.report_action.isEnabled()
    window.lock_book()
    assert not window.report_action.isEnabled()


def test_report_snapshot_stays_unchanged_after_edit_and_closed_session_is_refused(session):
    import pytest

    from personal_finance.domain.errors import VaultError

    populate(session)
    service = Analytics(session.ledger, session.mutex, session.ensure_open)
    before = fingerprints(session)
    report = service.report(PERIOD)
    assert fingerprints(session) == before
    current = session.ledger.operations()[0]
    session.ledger.delete(current.id, current.revision_id)
    assert report.operation_count == 2 and service.report(PERIOD).operation_count == 1
    session.close()
    with pytest.raises(VaultError):
        service.report(PERIOD)


def test_cancel_period_does_not_start_a_job(qtbot, session, monkeypatch):
    window = MainWindow()
    qtbot.addWidget(window)
    window.activate_session(session)
    monkeypatch.setattr(ReportPeriodDialog, "exec", lambda _: QDialog.DialogCode.Rejected)
    window.request_analytics()
    assert not window._busy and not window._jobs
    window.lock_book()


def test_stale_report_is_not_shown_after_lock(qtbot, session, monkeypatch):
    window = MainWindow()
    qtbot.addWidget(window)
    window.activate_session(session)
    window.lock_book()
    seen = []
    monkeypatch.setattr(IncomeExpenseDialog, "exec", lambda dialog: seen.append(dialog))
    window._show_analytics(session, (income_expense_report([], PERIOD), []))
    assert seen == []
