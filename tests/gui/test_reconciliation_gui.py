from datetime import date

from PySide6.QtCore import QDate, Qt, QThread
from PySide6.QtWidgets import QMessageBox

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
    window.navigation.setCurrentRow(3)
    window.balance_date.setDate(QDate(2026, 1, 15))
    window.show()
    worker_in_gui = []
    reconcile = session.ledger.reconcile

    def record(*args):
        worker_in_gui.append(QThread.isMainThread())
        return reconcile(*args)

    monkeypatch.setattr(session.ledger, "reconcile", record)

    def current():
        scroll = window._account_forms.stack.currentWidget()
        return scroll.widget() if scroll is not None else None

    qtbot.mouseClick(window.reconcile_button, Qt.MouseButton.LeftButton)
    dialog = current()
    assert isinstance(dialog, ReconciliationInputDialog) and not dialog.isWindow()
    dialog.statement.setText("90,00")
    dialog._validate()
    qtbot.waitUntil(lambda: isinstance(current(), ReconciliationResultDialog), timeout=15000)
    result = current()
    assert not result.isWindow() and result.result_snapshot.difference_minor == -1000
    result.reject()
    assert worker_in_gui == [False]
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
