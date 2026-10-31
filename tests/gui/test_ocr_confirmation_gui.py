from dataclasses import replace
from datetime import date
from uuid import uuid4

import pytest
from PySide6.QtCore import QDate, QThread
from PySide6.QtWidgets import QDialog, QMessageBox
from sqlalchemy import text

from personal_finance.application.ocr_cards import OcrCardPreview, OcrHistoryPreview
from personal_finance.application.ocr_importing import OcrImporting
from personal_finance.domain.ledger import Operation, OperationDraft, OperationKind
from personal_finance.domain.models import Account, AccountKind, Category, CategoryKind, Money
from personal_finance.domain.ocr import LoadedOcrImage
from personal_finance.domain.ocr_cards import CardCorrections, CashDirection, review_card
from personal_finance.domain.ocr_imports import OcrImportPreview, OcrImportStatus
from personal_finance.infrastructure.ocr.t_bank import TBankCardAdapter
from personal_finance.infrastructure.ocr.t_bank_history import TBankHistoryAdapter
from personal_finance.presentation.main_window import MainWindow
from personal_finance.presentation.ocr_confirmation import OcrConfirmationDialog, OcrOperationDialog
from personal_finance.presentation.ocr_history import OcrHistoryDialog
from personal_finance.presentation.ocr_preview import OcrPreviewDialog
from tests.ocr_fixtures import LINES, document, history_document, png_image


@pytest.fixture
def catalogs():
    accounts = [
        Account(str(uuid4()), "Вымышленный счёт " + str(i), AccountKind.CASH, None) for i in (1, 2)
    ]
    categories = [
        Category(str(uuid4()), "Вымышленная категория " + kind.value, kind) for kind in CategoryKind
    ]
    return accounts, categories


def source_preview(*, incoming=False):
    image = png_image()
    lines = (LINES[0], LINES[1], "+1 234,56 RUB" if incoming else LINES[2], *LINES[3:])
    card = TBankCardAdapter().parse(document(lines, image_sha256=image.sha256))
    return OcrCardPreview(LoadedOcrImage("3" * 64, image), card)


def reviewed(value):
    return review_card(
        value.card,
        CardCorrections(
            "1234,56",
            "RUB",
            value.card.direction.value,
            "27.10.2026",
            "14:35",
            "Вымышленный пример",
        ),
        confirmed_fields=frozenset({"amount", "currency", "direction", "occurred_on"}),
        single_completed_operation=True,
    )


def make_selection(catalogs):
    accounts, categories = catalogs
    value = source_preview()
    category = next(c for c in categories if c.kind == CategoryKind.EXPENSE)
    draft = OperationDraft(
        OperationKind.EXPENSE, date(2026, 10, 27), accounts[0].id, Money(123456), category.id
    )
    return OcrImporting.prepare(value, reviewed(value), accounts[0].id, draft)


def choose_expense(dialog, accounts, categories):
    dialog.kind.setCurrentIndex(dialog.kind.findData(OperationKind.EXPENSE.value))
    dialog.source_account.setCurrentIndex(dialog.source_account.findData(accounts[0].id))
    category = next(c for c in categories if c.kind == CategoryKind.EXPENSE)
    dialog.category.setCurrentIndex(dialog.category.findData(category.id))


def test_ocr_operation_requires_explicit_kind_and_source_account(qtbot, catalogs, monkeypatch):
    accounts, categories = catalogs
    value = source_preview()
    dialog = OcrOperationDialog(value, reviewed(value), accounts, categories, [])
    qtbot.addWidget(dialog)
    assert dialog.kind.currentData() is None and dialog.source_account.currentData() is None
    assert dialog.kind.findData(OperationKind.OPENING.value) == -1
    errors = []
    monkeypatch.setattr(
        QMessageBox, "warning", lambda parent, title, message: errors.append(message)
    )
    dialog._validate()
    assert dialog.result_selection is None and dialog.result() != QDialog.DialogCode.Accepted
    dialog.kind.setCurrentIndex(dialog.kind.findData(OperationKind.EXPENSE.value))
    dialog.account.setCurrentIndex(1)
    category = next(c for c in categories if c.kind == CategoryKind.EXPENSE)
    dialog.category.setCurrentIndex(dialog.category.findData(category.id))
    dialog._validate()
    assert len(errors) == 2 and dialog.result_selection is None
    choose_expense(dialog, accounts, categories)
    dialog._validate()
    assert dialog.result_selection.draft.kind == OperationKind.EXPENSE
    assert dialog.result_selection.account_id == accounts[0].id


@pytest.mark.parametrize("incoming", [False, True])
def test_transfer_keeps_explicit_source_side_and_exact_movement(qtbot, catalogs, incoming):
    accounts, categories = catalogs
    value = source_preview(incoming=incoming)
    dialog = OcrOperationDialog(value, reviewed(value), accounts, categories, [])
    qtbot.addWidget(dialog)
    dialog.kind.setCurrentIndex(dialog.kind.findData(OperationKind.TRANSFER.value))
    source = accounts[1] if incoming else accounts[0]
    dialog.source_account.setCurrentIndex(dialog.source_account.findData(source.id))
    dialog.account.setCurrentIndex(dialog.account.findData(accounts[0].id))
    dialog.destination.setCurrentIndex(dialog.destination.findData(accounts[1].id))
    dialog.fee.setText("10,00")
    dialog.fee_category.setCurrentIndex(1)
    if not incoming:
        dialog.amount.setText("1224,56")
    dialog._validate()
    selection = dialog.result_selection
    assert selection is not None and selection.account_id == source.id
    assert selection.draft.kind == OperationKind.TRANSFER and selection.draft.fee.minor == 1000


@pytest.mark.parametrize("change", ["amount", "date"])
def test_operation_cannot_silently_change_checked_money_or_date(
    qtbot, catalogs, monkeypatch, change
):
    accounts, categories = catalogs
    value = source_preview()
    dialog = OcrOperationDialog(value, reviewed(value), accounts, categories, [])
    qtbot.addWidget(dialog)
    choose_expense(dialog, accounts, categories)
    if change == "amount":
        dialog.amount.setText("1234,57")
    else:
        dialog.occurred_on.setDate(QDate(2026, 10, 28))
    errors = []
    monkeypatch.setattr(QMessageBox, "warning", lambda *args: errors.append(args[-1]))
    dialog._validate()
    assert dialog.result_selection is None and dialog.result() != QDialog.DialogCode.Accepted
    assert errors and "1234" not in errors[0]


def test_new_confirmation_requires_separate_financial_acknowledgment(qtbot, catalogs):
    accounts, categories = catalogs
    selection = make_selection(catalogs)
    dialog = OcrConfirmationDialog(
        OcrImportPreview(selection, OcrImportStatus.NEW), accounts, categories, []
    )
    qtbot.addWidget(dialog)
    assert not dialog.confirm.isEnabled() and dialog.result_selection is None
    dialog.verified.setChecked(True)
    dialog._validate()
    assert dialog.result() == QDialog.DialogCode.Accepted and dialog.result_selection == selection


def candidate(selection):
    return Operation(
        str(uuid4()), str(uuid4()), replace(selection.draft, note="Вымышленный <b>текст</b>"), False
    )


def test_possible_duplicate_requires_explicit_separate_choice(qtbot, catalogs):
    accounts, categories = catalogs
    selection = make_selection(catalogs)
    dialog = OcrConfirmationDialog(
        OcrImportPreview(selection, OcrImportStatus.POSSIBLE, (candidate(selection),)),
        accounts,
        categories,
        [],
    )
    qtbot.addWidget(dialog)
    dialog.verified.setChecked(True)
    assert not dialog.confirm.isEnabled()
    dialog.separate.setChecked(True)
    assert not dialog.verified.isChecked()
    dialog.verified.setChecked(True)
    dialog._validate()
    assert dialog.result_selection.acknowledged_duplicate
    assert dialog.result_selection.existing_operation_id is None


def test_link_uses_candidate_snapshot_and_resets_ack_on_change(qtbot, catalogs):
    accounts, categories = catalogs
    selection = make_selection(catalogs)
    operation = candidate(selection)
    dialog = OcrConfirmationDialog(
        OcrImportPreview(selection, OcrImportStatus.POSSIBLE, (operation,)),
        accounts,
        categories,
        [],
    )
    qtbot.addWidget(dialog)
    dialog.link.setChecked(True)
    dialog.verified.setChecked(True)
    assert not dialog.confirm.isEnabled()
    dialog.candidate.setCurrentIndex(1)
    assert not dialog.verified.isChecked() and "<b>текст</b>" in dialog.summary.text()
    dialog.verified.setChecked(True)
    dialog._validate()
    linked = dialog.result_selection
    assert linked.draft == operation.draft and linked.existing_operation_id == operation.id
    assert (
        linked.expected_revision_id == operation.revision_id and not linked.acknowledged_duplicate
    )


@pytest.mark.parametrize("status", [OcrImportStatus.DUPLICATE, OcrImportStatus.CONFLICT])
def test_exact_repeat_or_conflict_has_no_confirmation_escape(qtbot, catalogs, monkeypatch, status):
    accounts, categories = catalogs
    selection = make_selection(catalogs)
    dialog = OcrConfirmationDialog(OcrImportPreview(selection, status), accounts, categories, [])
    qtbot.addWidget(dialog)
    dialog.separate.setChecked(True)
    dialog.verified.setChecked(True)
    assert not dialog.confirm.isEnabled()
    monkeypatch.setattr(QMessageBox, "warning", lambda *a: None)
    dialog._validate()
    assert dialog.result_selection is None and dialog.result() != QDialog.DialogCode.Accepted


def test_regular_review_never_requests_financial_write(qtbot):
    value = source_preview()
    dialog = OcrPreviewDialog(value, allow_financial_prepare=True)
    qtbot.addWidget(dialog)
    for check in dialog.checks.values():
        check.setChecked(True)
    dialog.finish.click()
    assert dialog.reviewed is not None and not dialog.financial_requested
    another = OcrPreviewDialog(value, allow_financial_prepare=True)
    qtbot.addWidget(another)
    for check in another.checks.values():
        check.setChecked(True)
    another.prepare_button.click()
    assert another.reviewed is not None and another.financial_requested


def test_history_prepares_only_selected_review_and_clear_blocks_it(qtbot):
    image = png_image()
    source = LoadedOcrImage("3" * 64, image)
    history = TBankHistoryAdapter().parse(history_document(image_sha256=image.sha256))
    dialog = OcrHistoryDialog(OcrHistoryPreview(source, history), allow_financial_prepare=True)
    qtbot.addWidget(dialog)
    assert not dialog.prepare_button.isEnabled()
    card = history.cards[0]
    check = review_card(
        card,
        CardCorrections("1234,56", "RUB", CashDirection.OUT, "27.10.2026", "", "Магазин"),
        confirmed_fields=frozenset({"amount", "currency", "direction", "occurred_on"}),
        single_completed_operation=True,
    )
    dialog.reviews[0] = check
    dialog._refresh()
    dialog.table.selectRow(0)
    assert dialog.prepare_button.isEnabled()
    dialog.table.clearSelection()
    assert not dialog.prepare_button.isEnabled()
    dialog.prepare_selected()
    assert dialog.selected_for_financial is None
    dialog.table.selectRow(0)
    dialog.prepare_selected()
    assert dialog.result() == QDialog.DialogCode.Accepted
    assert dialog.selected_for_financial[1] is check
    assert dialog.selected_for_financial[0].card is card


@pytest.mark.parametrize("mode", ["closed", "legacy", "current"])
def test_main_window_preparation_is_gated_by_current_book(qtbot, session, monkeypatch, mode):
    window = MainWindow()
    qtbot.addWidget(window)
    if mode == "legacy":
        monkeypatch.setattr(session, "ocr_importing", None)
    if mode != "closed":
        window.activate_session(session)
    seen = []

    def show(dialog):
        seen.append(dialog.prepare_button is not None)
        return QDialog.DialogCode.Rejected

    monkeypatch.setattr(OcrPreviewDialog, "exec", show)
    window._show_ocr_preview(source_preview())
    assert seen == [mode == "current"]


@pytest.mark.parametrize("accept_final", [False, True])
def test_full_ui_chain_keeps_preview_readonly_and_confirms_in_worker(
    qtbot, qapp, session, monkeypatch, accept_final
):
    account = session.catalog.add_account("Вымышленный OCR счёт", AccountKind.CASH)
    window = MainWindow()
    qtbot.addWidget(window)
    window.activate_session(session)
    seen = []
    service = session.ocr_importing
    original_preview, original_confirm = service.preview, service.confirm

    def check_worker(selection):
        assert QThread.currentThread() != qapp.thread()
        assert not session.ledger.operations()
        seen.append("preview-worker")
        return original_preview(selection)

    def confirm_worker(selection, *, command_id):
        assert QThread.currentThread() != qapp.thread()
        seen.append("confirm-worker")
        return original_confirm(selection, command_id=command_id)

    monkeypatch.setattr(service, "preview", check_worker)
    monkeypatch.setattr(service, "confirm", confirm_worker)

    def review(dialog):
        for check in dialog.checks.values():
            check.setChecked(True)
        dialog._request_financial()
        return dialog.result()

    def prepare(dialog):
        assert QThread.currentThread() == qapp.thread() and not window._busy and not window._jobs
        choose_expense(dialog, window._account_records, session.catalog.categories())
        dialog._validate()
        return dialog.result()

    def confirm(dialog):
        assert QThread.currentThread() == qapp.thread() and not window._busy and not window._jobs
        assert not window._job_callbacks and not session.ledger.operations()
        seen.append("final")
        if accept_final:
            dialog.verified.setChecked(True)
            dialog._validate()
        else:
            dialog.reject()
        return dialog.result()

    monkeypatch.setattr(OcrPreviewDialog, "exec", review)
    monkeypatch.setattr(OcrOperationDialog, "exec", prepare)
    monkeypatch.setattr(OcrConfirmationDialog, "exec", confirm)
    failures = []
    monkeypatch.setattr(QMessageBox, "warning", lambda *a: failures.append(a[-1]))
    window._show_ocr_preview(source_preview())
    qtbot.waitUntil(lambda: "final" in seen, timeout=10000)
    qtbot.waitUntil(lambda: not window._busy, timeout=10000)
    assert not failures
    assert len(session.ledger.operations()) == int(accept_final)
    with session.engine.connect() as connection:
        assert connection.execute(text("SELECT count(*) FROM ocr_records")).scalar_one() == int(
            accept_final
        )
    assert ("confirm-worker" in seen) == accept_final
    assert account.id == window._account_records[0].id


def test_missing_amount_evidence_allows_review_but_blocks_financial_prepare(qtbot):
    value = source_preview()
    card = replace(value.card, amount=replace(value.card.amount, word_indices=()))
    dialog = OcrPreviewDialog(replace(value, card=card), allow_financial_prepare=True)
    qtbot.addWidget(dialog)
    for check in dialog.checks.values():
        check.setChecked(True)
    assert dialog.finish.isEnabled() and not dialog.prepare_button.isEnabled()
    dialog._request_financial()
    assert dialog.reviewed is None and not dialog.financial_requested
    dialog.finish.click()
    assert dialog.reviewed is not None and not dialog.financial_requested


def test_history_explains_missing_source_amount_area(qtbot):
    image = png_image()
    source = LoadedOcrImage("3" * 64, image)
    history = TBankHistoryAdapter().parse(history_document(image_sha256=image.sha256))
    card = replace(history.cards[0], amount=replace(history.cards[0].amount, word_indices=()))
    history = replace(history, cards=(card, *history.cards[1:]))
    dialog = OcrHistoryDialog(OcrHistoryPreview(source, history), allow_financial_prepare=True)
    qtbot.addWidget(dialog)
    assert "Выбрана строка 1" in dialog.selection_note.text()
    assert "не выделена область" in dialog.selection_note.text()
    assert not dialog.prepare_button.isEnabled()


def test_cancel_operation_form_does_not_start_duplicate_preview(qtbot, session, monkeypatch):
    session.catalog.add_account("Вымышленный счёт отмены", AccountKind.CASH)
    window = MainWindow()
    qtbot.addWidget(window)
    window.activate_session(session)
    value = source_preview()
    calls = []
    monkeypatch.setattr(session.ocr_importing, "preview", lambda selection: calls.append(selection))

    def cancel(dialog):
        assert dialog.kind.currentData() is None and dialog.source_account.currentData() is None
        dialog.reject()
        return dialog.result()

    monkeypatch.setattr(OcrOperationDialog, "exec", cancel)
    window._prepare_ocr_operation(session, value, reviewed(value))
    assert not window._busy and not window._jobs and not calls
    assert not session.ledger.operations()
    with session.engine.connect() as connection:
        assert connection.execute(text("SELECT count(*) FROM ocr_records")).scalar_one() == 0


def test_stale_session_callback_does_not_open_confirmation(qtbot, session, catalogs, monkeypatch):
    window = MainWindow()
    qtbot.addWidget(window)
    seen = []
    monkeypatch.setattr(OcrConfirmationDialog, "exec", lambda dialog: seen.append(dialog))
    window._show_ocr_confirmation(
        session, OcrImportPreview(make_selection(catalogs), OcrImportStatus.NEW)
    )
    assert not seen and not window._busy
