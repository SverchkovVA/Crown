from datetime import date

from PySide6.QtCore import QDate, Qt
from PySide6.QtWidgets import QDialog, QMessageBox

from personal_finance.application import vault as vault_module
from personal_finance.domain.ledger import OperationDraft, OperationKind
from personal_finance.domain.models import AccountKind, CategoryKind, Money
from personal_finance.domain.periods import AccountingMonth
from personal_finance.infrastructure.persistence import migration_runner
from personal_finance.presentation.main_window import MainWindow
from personal_finance.presentation.periods import (
    PeriodChangeDialog,
    PeriodHistoryDialog,
    PeriodPage,
)
from tests.conftest import PASSWORD

MONTH = AccountingMonth(2026, 1)


def test_reopening_form_requires_reason_and_cancel_does_not_submit(qtbot, monkeypatch):
    messages = []
    monkeypatch.setattr(QMessageBox, "warning", lambda *args: messages.append(args[-1]))
    dialog = PeriodChangeDialog(MONTH, False)
    qtbot.addWidget(dialog)
    dialog.show()
    dialog._validate()
    assert dialog.result_reason is None and "причину" in messages[-1]
    dialog.reason.setPlainText("  Исправление расхода  ")
    dialog._validate()
    assert dialog.result() == QDialog.DialogCode.Accepted
    assert dialog.result_reason == "Исправление расхода"
    cancelled = PeriodChangeDialog(MONTH, True)
    qtbot.addWidget(cancelled)
    cancelled.reject()
    assert cancelled.result_reason is None


def test_page_tracks_selected_month_and_history(qtbot, session):
    closed = session.periods.close(MONTH, reason="Сверено")
    opened = session.periods.reopen(MONTH, closed.revision_id, reason="Правка")
    other = session.periods.close(AccountingMonth(2026, 2))
    page = PeriodPage()
    qtbot.addWidget(page)
    page.set_states(session.periods.states(), True)
    page.show()
    page.table.selectRow(0)
    assert page.month() == other.month and page.reopen_button.isEnabled()
    page.table.selectRow(1)
    assert page.month() == MONTH and page.close_button.isEnabled()
    assert not page.reopen_button.isEnabled() and page.history_button.isEnabled()
    history = PeriodHistoryDialog(session.periods.history(MONTH))
    qtbot.addWidget(history)
    assert history.model.rowCount() == 2
    assert history.model.item(0, 2).text() == "Закрыт"
    assert history.model.item(1, 3).text() == opened.reason


def test_main_window_closes_reopens_in_worker_and_clears_state(qtbot, session, monkeypatch):
    account = session.catalog.add_account("Тестовый счёт", AccountKind.CASH)
    category = next(c.id for c in session.catalog.categories() if c.kind == CategoryKind.EXPENSE)
    session.ledger.add(
        OperationDraft(OperationKind.EXPENSE, date(2026, 1, 15), account.id, Money(1000), category)
    )
    window = MainWindow()
    qtbot.addWidget(window)
    window.activate_session(session)
    window.navigation.setCurrentRow(4)
    window.show()
    window.period_page.month_edit.setDate(QDate(2026, 1, 1))

    def accept(dialog):
        dialog.reason.setPlainText("Синтетическая проверка")
        dialog._validate()
        return dialog.result()

    monkeypatch.setattr(PeriodChangeDialog, "exec", accept)
    qtbot.mouseClick(window.period_page.close_button, Qt.MouseButton.LeftButton)
    qtbot.waitUntil(lambda: not window._busy, timeout=15000)
    assert MONTH in window._closed_months
    assert window.period_page.reopen_button.isEnabled()
    window.operation_table.selectRow(0)
    assert not window.edit_operation_button.isEnabled()
    assert not window.delete_operation_button.isEnabled()
    assert window.history_operation_button.isEnabled()
    qtbot.mouseClick(window.period_page.reopen_button, Qt.MouseButton.LeftButton)
    qtbot.waitUntil(lambda: not window._busy, timeout=15000)
    window.operation_table.selectRow(0)
    assert MONTH not in window._closed_months and window.edit_operation_button.isEnabled()
    assert len(session.periods.history(MONTH)) == 2
    session.check_integrity()
    window.lock_book()
    assert not window._closed_months and window.period_page.model.rowCount() == 0
    assert not window.period_page.close_button.isEnabled()


def test_legacy5_keeps_periods_unavailable_but_refunds_supported(qtbot, tmp_path, monkeypatch):
    with monkeypatch.context() as patch:
        patch.setattr(
            vault_module,
            "initialize_schema",
            lambda e: migration_runner.initialize_schema(e, target="0005"),
        )
        old, _ = vault_module.create_vault(
            tmp_path / "old5.pfvault", "Старая тестовая книга", PASSWORD
        )
    try:
        window = MainWindow()
        qtbot.addWidget(window)
        window.activate_session(old)
        assert old.ledger.supports_refunds and old.periods is None
        assert not window.period_page.close_button.isEnabled()
        assert "в новую папку" in window.period_page.status.text()
        window.lock_book()
    finally:
        old.close()
