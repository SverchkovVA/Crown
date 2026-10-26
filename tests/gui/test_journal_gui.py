from datetime import date

import pytest
from PySide6.QtCore import QDate, Qt
from PySide6.QtWidgets import QDialog, QMessageBox

from personal_finance.domain.ledger import OperationKind
from personal_finance.domain.models import AccountKind, CategoryKind, Money
from personal_finance.presentation import main_window as window_module
from personal_finance.presentation.ledger_dialogs import OperationDialog, format_money
from personal_finance.presentation.main_window import MainWindow


@pytest.fixture
def financial_data(session):
    account = session.catalog.add_account("Проверочные наличные", AccountKind.CASH)
    target = session.catalog.add_account("Проверочный банк", AccountKind.DEBIT)
    income = next(c for c in session.catalog.categories() if c.kind == CategoryKind.INCOME)
    expense = next(c for c in session.catalog.categories() if c.kind == CategoryKind.EXPENSE)
    return account, target, income, expense


@pytest.mark.parametrize(
    "kind,amount,expected",
    [
        (OperationKind.EXPENSE, "1 234,56", 123456),
        (OperationKind.INCOME, "1 234,56", 123456),
        (OperationKind.OPENING, "0,00", 0),
        (OperationKind.OPENING, "-12,34", -1234),
        (OperationKind.TRANSFER, "1 234,56", 123456),
    ],
)
def test_operation_dialog_builds_exact_financial_draft(
    qtbot,
    session,
    financial_data,
    kind,
    amount,
    expected,
):
    account, target, income, expense = financial_data
    dialog = OperationDialog(
        session.catalog.accounts(),
        session.catalog.categories(),
        session.catalog.institutions(),
        kind=kind,
        account_id=account.id,
    )
    qtbot.addWidget(dialog)
    dialog.show()
    dialog.occurred_on.setDate(QDate(2026, 10, 26))
    dialog.amount.setText(amount)
    if kind in (OperationKind.EXPENSE, OperationKind.INCOME):
        category = expense if kind == OperationKind.EXPENSE else income
        dialog.category.setCurrentIndex(dialog.category.findData(category.id))
    elif kind == OperationKind.TRANSFER:
        dialog.destination.setCurrentIndex(dialog.destination.findData(target.id))
        dialog.fee.setText("2,50")
        dialog.fee_category.setCurrentIndex(dialog.fee_category.findData(expense.id))
    dialog._validate()
    assert dialog.result() == QDialog.DialogCode.Accepted
    assert dialog.result_draft is not None
    assert dialog.result_draft.amount.minor == expected
    assert dialog.result_draft.kind == kind
    assert dialog.result_draft.occurred_on == date(2026, 10, 26)
    assert sum(post.amount.minor for post in dialog.result_draft.postings()) == 0
    if kind == OperationKind.TRANSFER:
        assert dialog.result_draft.fee.minor == 250


def test_operation_dialog_rejects_invalid_money_without_acceptance(
    qtbot,
    session,
    financial_data,
    monkeypatch,
):
    account, _, _, expense = financial_data
    errors = []
    monkeypatch.setattr(QMessageBox, "warning", lambda *args: errors.append(args[-1]))
    dialog = OperationDialog(
        session.catalog.accounts(),
        session.catalog.categories(),
        session.catalog.institutions(),
        account_id=account.id,
    )
    qtbot.addWidget(dialog)
    dialog.amount.setText("NaN")
    dialog.category.setCurrentIndex(dialog.category.findData(expense.id))
    dialog._validate()
    assert dialog.result() != QDialog.DialogCode.Accepted
    assert dialog.result_draft is None
    assert errors


def test_gui_add_edit_delete_history_and_clear_book(
    qtbot,
    session,
    financial_data,
    monkeypatch,
):
    account, _, _, expense = financial_data
    errors = []
    monkeypatch.setattr(QMessageBox, "warning", lambda *args: errors.append(args[-1]))
    window = MainWindow()
    qtbot.addWidget(window)
    window.activate_session(session)
    window.balance_date.setDate(QDate(2026, 10, 26))
    window.show()
    window.navigation.setCurrentRow(2)
    value = ["10,00"]

    def accept(dialog):
        assert dialog.kind.currentData() == OperationKind.EXPENSE.value
        dialog.account.setCurrentIndex(dialog.account.findData(account.id))
        dialog.category.setCurrentIndex(dialog.category.findData(expense.id))
        dialog.occurred_on.setDate(QDate(2026, 10, 26))
        dialog.amount.setText(value[0])
        dialog._validate()
        return dialog.result()

    monkeypatch.setattr(OperationDialog, "exec", accept)
    qtbot.mouseClick(window.add_operation_button, Qt.MouseButton.LeftButton)
    qtbot.waitUntil(lambda: not window._busy, timeout=15000)
    assert not errors
    assert window.operation_model.rowCount() == 1
    assert window.account_model.item(0, 4).text() == "-10,00"
    window.operation_table.selectRow(0)
    assert window.edit_operation_button.isEnabled()
    value[0] = "20,00"
    qtbot.mouseClick(window.edit_operation_button, Qt.MouseButton.LeftButton)
    qtbot.waitUntil(lambda: not window._busy, timeout=15000)
    assert window.account_model.item(0, 4).text() == "-20,00"
    window.operation_table.selectRow(0)
    seen = []

    class HistoryProbe:
        def __init__(self, versions, parent):
            seen.append(versions)

        def exec(self):
            return QDialog.DialogCode.Rejected

    monkeypatch.setattr(window_module, "OperationHistoryDialog", HistoryProbe)
    qtbot.mouseClick(window.history_operation_button, Qt.MouseButton.LeftButton)
    assert len(seen[-1]) == 2
    monkeypatch.setattr(QMessageBox, "question", lambda *args: QMessageBox.StandardButton.Yes)
    qtbot.mouseClick(window.delete_operation_button, Qt.MouseButton.LeftButton)
    qtbot.waitUntil(lambda: not window._busy, timeout=15000)
    assert window.operation_model.rowCount() == 0
    assert window.account_model.item(0, 4).text() == "0,00"
    window.show_deleted.setChecked(True)
    assert window.operation_model.rowCount() == 1
    assert window.operation_model.item(0, 7).text() == "Удалена"
    window.operation_table.selectRow(0)
    assert not window.edit_operation_button.isEnabled()
    assert not window.delete_operation_button.isEnabled()
    assert window.history_operation_button.isEnabled()
    qtbot.mouseClick(window.history_operation_button, Qt.MouseButton.LeftButton)
    assert len(seen[-1]) == 3
    session.check_integrity()
    window.lock_book()
    assert window.session is None
    assert window.operation_model.rowCount() == 0
    assert window._journal_operations == []
    assert window._account_records == []
    assert not errors


@pytest.mark.parametrize("minor", [0, 1, -1, 123456789, -(2**63 - 1)])
def test_russian_money_format_round_trips_without_float(minor):
    money = Money(minor)
    assert Money.parse(format_money(money, include_currency=False)) == money
