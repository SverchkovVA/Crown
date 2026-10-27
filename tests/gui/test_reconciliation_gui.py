from datetime import date

from PySide6.QtCore import QDate, Qt, QThread
from PySide6.QtWidgets import QApplication, QMessageBox

from personal_finance.application import vault as vault_module
from personal_finance.domain.ledger import OperationDraft, OperationKind
from personal_finance.domain.models import MAX_MINOR, AccountKind, Money
from personal_finance.infrastructure.persistence import migration_runner
from personal_finance.presentation.main_window import MainWindow
from personal_finance.presentation.reconciliation import (
    ReconciliationInputDialog,
    ReconciliationResultDialog,
)
from tests.conftest import PASSWORD

DAY = date(2026, 1, 15)


def test_input_accepts_exact_negative_balance_and_rejects_extra_precision(
    qtbot, session, monkeypatch
):
    account = session.catalog.add_account("Счёт", AccountKind.CASH)
    dialog = ReconciliationInputDialog(session.catalog.accounts(), DAY)
    qtbot.addWidget(dialog)
    messages = []
    monkeypatch.setattr(QMessageBox, "warning", lambda *args: messages.append(args[-1]))
    dialog.statement.setText("1,001")
    dialog._validate()
    assert dialog.result_request is None and messages
    dialog.statement.setText("-1 234,56")
    dialog._validate()
    assert dialog.result_request == (account.id, DAY, Money(-123456))


def test_result_formats_extreme_difference_without_money_overflow(qtbot, session):
    account = session.catalog.add_account("Крайний счёт", AccountKind.CASH)
    session.ledger.add(OperationDraft(OperationKind.OPENING, DAY, account.id, Money(-MAX_MINOR)))
    result = session.ledger.reconcile(account.id, DAY, Money(MAX_MINOR))
    dialog = ReconciliationResultDialog(result, account.name)
    qtbot.addWidget(dialog)
    assert "184 467 440 737 095 516,14 RUB" in dialog.summary.text()
    assert "больше" in dialog.summary.text() and dialog.model.rowCount() == 1


def test_main_action_runs_in_worker_and_keeps_ledger_unchanged(qtbot, session, monkeypatch):
    account = session.catalog.add_account("Проверочные наличные", AccountKind.CASH)
    opening = session.ledger.add(
        OperationDraft(OperationKind.OPENING, DAY, account.id, Money(10000))
    )
    window = MainWindow()
    qtbot.addWidget(window)
    window.activate_session(session)
    window.navigation.setCurrentRow(1)
    window.balance_date.setDate(QDate(2026, 1, 15))
    window.show()
    results = []
    threads = []
    reconcile = session.ledger.reconcile

    def record(*args):
        threads.append(QThread.currentThread())
        return reconcile(*args)

    monkeypatch.setattr(session.ledger, "reconcile", record)

    def accept(dialog):
        dialog.statement.setText("90,00")
        dialog._validate()
        return dialog.result()

    def show(dialog):
        results.append(dialog.result_snapshot)
        return 0

    monkeypatch.setattr(ReconciliationInputDialog, "exec", accept)
    monkeypatch.setattr(ReconciliationResultDialog, "exec", show)
    qtbot.mouseClick(window.reconcile_button, Qt.MouseButton.LeftButton)
    qtbot.waitUntil(lambda: not window._busy, timeout=15000)
    assert len(results) == 1 and results[0].difference_minor == -1000
    assert threads and threads[0] != QApplication.instance().thread()
    assert session.ledger.operations() == [opening]
    session.check_integrity()
    window.lock_book()
    assert not window.reconcile_button.isEnabled()


def test_catalog_only_schema_cannot_start_reconciliation(qtbot, tmp_path, monkeypatch):
    with monkeypatch.context() as patch:
        patch.setattr(
            vault_module,
            "initialize_schema",
            lambda e: migration_runner.initialize_schema(e, target="0002"),
        )
        old, _ = vault_module.create_vault(tmp_path / "old2.pfvault", "Старая книга", PASSWORD)
    try:
        window = MainWindow()
        qtbot.addWidget(window)
        window.activate_session(old)
        assert not window.reconcile_button.isEnabled()
        window.lock_book()
    finally:
        old.close()
