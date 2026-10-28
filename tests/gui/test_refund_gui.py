from dataclasses import replace
from datetime import date, timedelta

import pytest
from PySide6.QtCore import QDate, Qt
from PySide6.QtWidgets import QDialog, QMessageBox

from personal_finance.application import vault as vault_module
from personal_finance.domain.ledger import OperationDraft, OperationKind, OperationPart
from personal_finance.domain.models import AccountKind, CategoryKind, Money
from personal_finance.infrastructure.persistence import migration_runner
from personal_finance.presentation.ledger_dialogs import OperationDialog, OperationHistoryDialog
from personal_finance.presentation.main_window import MainWindow
from tests.conftest import PASSWORD

DAY = date(2026, 10, 27)


@pytest.fixture
def refund_data(session):
    account = session.catalog.add_account("Возвратный счёт", AccountKind.CASH)
    categories = [c.id for c in session.catalog.categories() if c.kind == CategoryKind.EXPENSE]
    purchase = session.ledger.add(
        OperationDraft(
            OperationKind.EXPENSE,
            DAY,
            account.id,
            Money(10000),
            categories[0],
            note="Исходный тестовый расход",
        )
    )
    return purchase, categories


def refund_form(session, *, existing=None):
    return OperationDialog(
        session.catalog.accounts(),
        session.catalog.categories(),
        session.catalog.institutions(),
        existing=existing,
        kind=OperationKind.REFUND,
        allow_refunds=True,
        refund_candidates=session.ledger.refund_candidates(
            excluding_refund_id=existing.id if existing else None
        ),
    )


def test_refund_form_prefills_source_and_allows_partial_receipt(qtbot, session, refund_data):
    purchase, categories = refund_data
    dialog = refund_form(session)
    qtbot.addWidget(dialog)
    dialog.show()
    assert dialog.purchase.currentData() == purchase.id
    assert dialog.amount.text() == "100,00"
    assert "100,00 RUB" in dialog.refund_hint.text()
    assert dialog.category.currentData() == categories[0]
    assert not dialog.split.isVisible()
    dialog.amount.setText("25,00")
    dialog.occurred_on.setDate(QDate(2026, 10, 28))
    dialog._validate()
    value = dialog.result_draft
    assert value is not None and dialog.result() == QDialog.DialogCode.Accepted
    assert value.refund_of == purchase.id and value.refund_of_revision_id == purchase.revision_id
    assert value.amount == Money(2500) and value.kind == OperationKind.REFUND
    session.ledger.add(value)
    assert session.ledger.refund_candidates()[0].amount_remaining == Money(7500)


def test_fully_refunded_purchase_can_be_edited_using_own_capacity(qtbot, session, refund_data):
    purchase, categories = refund_data
    value = OperationDraft(
        OperationKind.REFUND,
        DAY + timedelta(days=1),
        purchase.draft.account_id,
        Money(10000),
        categories[0],
        refund_of=purchase.id,
        refund_of_revision_id=purchase.revision_id,
    )
    returned = session.ledger.add(value)
    assert session.ledger.refund_candidates()[0].amount_remaining == Money(0)
    dialog = refund_form(session, existing=returned)
    qtbot.addWidget(dialog)
    dialog._validate()
    assert dialog.result_draft == value


def test_split_purchase_prefills_refund_parts_and_single_refund_edit_stays_single(
    qtbot, session, refund_data
):
    purchase, categories = refund_data
    corrected = session.ledger.replace(
        purchase.id,
        purchase.revision_id,
        replace(
            purchase.draft,
            category_id=None,
            parts=(
                OperationPart(categories[0], Money(7000)),
                OperationPart(categories[1], Money(3000)),
            ),
        ),
    )
    dialog = refund_form(session)
    qtbot.addWidget(dialog)
    assert dialog.split.isChecked()
    dialog._validate()
    assert dialog.result_draft.parts == corrected.draft.parts
    single = session.ledger.add(
        OperationDraft(
            OperationKind.REFUND,
            DAY + timedelta(days=1),
            purchase.draft.account_id,
            Money(1000),
            categories[0],
            refund_of=purchase.id,
            refund_of_revision_id=corrected.revision_id,
        )
    )
    editing = refund_form(session, existing=single)
    qtbot.addWidget(editing)
    assert not editing.split.isChecked()
    editing._validate()
    assert editing.result_draft == single.draft


def test_refund_form_rejects_more_than_remaining(qtbot, session, refund_data, monkeypatch):
    purchase, categories = refund_data
    session.ledger.add(
        OperationDraft(
            OperationKind.REFUND,
            DAY + timedelta(days=1),
            purchase.draft.account_id,
            Money(8000),
            categories[0],
            refund_of=purchase.id,
            refund_of_revision_id=purchase.revision_id,
        )
    )
    errors = []
    monkeypatch.setattr(QMessageBox, "warning", lambda *args: errors.append(args[-1]))
    dialog = refund_form(session)
    qtbot.addWidget(dialog)
    dialog.amount.setText("30,00")
    dialog._validate()
    assert dialog.result_draft is None
    assert "остаток к возврату" in errors[-1]


def test_history_links_to_exact_expense_version_after_correction(qtbot, session, refund_data):
    purchase, categories = refund_data
    first = session.ledger.add(
        OperationDraft(
            OperationKind.REFUND,
            DAY + timedelta(days=1),
            purchase.draft.account_id,
            Money(1000),
            categories[0],
            refund_of=purchase.id,
            refund_of_revision_id=purchase.revision_id,
        )
    )
    source = session.ledger.replace(
        purchase.id,
        purchase.revision_id,
        replace(purchase.draft, amount=Money(11000), note="Исправленный тестовый расход"),
    )
    second = session.ledger.replace(
        first.id, first.revision_id, replace(first.draft, refund_of_revision_id=source.revision_id)
    )
    dialog = OperationHistoryDialog(
        [first, second],
        accounts=session.catalog.accounts(),
        categories=session.catalog.categories(),
        related_versions=session.ledger.history(purchase.id),
    )
    qtbot.addWidget(dialog)
    assert "Исправленный тестовый расход" in dialog.details.toPlainText()
    dialog.table.setCurrentCell(0, 0)
    assert "Исходный тестовый расход" in dialog.details.toPlainText()
    assert "Исправленный тестовый расход" not in dialog.details.toPlainText()


def test_gui_refund_action_changes_balance_and_clears_cache(
    qtbot, session, refund_data, monkeypatch
):
    purchase, _ = refund_data
    window = MainWindow()
    qtbot.addWidget(window)
    window.activate_session(session)
    window.balance_date.setDate(QDate(2026, 10, 28))
    window.navigation.setCurrentRow(0)
    window.show()
    window.operation_table.selectRow(0)
    assert window.refund_button.isEnabled()

    def accept(dialog):
        assert dialog.kind.currentData() == OperationKind.REFUND.value
        assert dialog.purchase.currentData() == purchase.id
        dialog.occurred_on.setDate(QDate(2026, 10, 28))
        dialog.amount.setText("30,00")
        dialog._validate()
        return dialog.result()

    monkeypatch.setattr(OperationDialog, "exec", accept)
    qtbot.mouseClick(window.refund_button, Qt.MouseButton.LeftButton)
    qtbot.waitUntil(lambda: not window._busy, timeout=15000)
    assert window.operation_model.rowCount() == 2
    assert window.account_model.item(0, 4).text() == "-70,00"
    assert any(window.operation_model.item(row, 1).text() == "Возврат" for row in range(2))
    session.check_integrity()
    window.lock_book()
    assert window._refund_candidates == [] and not window.refund_button.isEnabled()


def test_legacy_schema4_keeps_refund_action_unavailable(qtbot, tmp_path, monkeypatch):
    with monkeypatch.context() as patch:
        patch.setattr(
            vault_module,
            "initialize_schema",
            lambda engine: migration_runner.initialize_schema(engine, target="0004"),
        )
        source, _ = vault_module.create_vault(
            tmp_path / "legacy.pfvault", "Старая тестовая схема", PASSWORD
        )
    try:
        account = source.catalog.add_account("Счёт", AccountKind.CASH)
        category = next(c.id for c in source.catalog.categories() if c.kind == CategoryKind.EXPENSE)
        source.ledger.add(
            OperationDraft(OperationKind.EXPENSE, DAY, account.id, Money(1000), category)
        )
        window = MainWindow()
        qtbot.addWidget(window)
        window.activate_session(source)
        assert not window.refund_button.isEnabled()
        dialog = OperationDialog(
            source.catalog.accounts(),
            source.catalog.categories(),
            source.catalog.institutions(),
            allow_refunds=source.ledger.supports_refunds,
        )
        qtbot.addWidget(dialog)
        assert dialog.kind.findData(OperationKind.REFUND.value) == -1
        window.lock_book()
    finally:
        source.close()
