from dataclasses import replace
from pathlib import Path

import pytest
from PySide6.QtCore import QMimeData, QPointF, Qt, QUrl
from PySide6.QtGui import QDropEvent, QImage
from PySide6.QtWidgets import QDialog, QFileDialog

from personal_finance.application.ocr import Ocr
from personal_finance.application.ocr_cards import (
    CardRecognition,
    OcrCardPreview,
    OcrHistoryPreview,
)
from personal_finance.application.ocr_review import OcrReviewDefaults
from personal_finance.domain.models import AccountKind
from personal_finance.domain.ocr import LoadedOcrImage
from personal_finance.domain.ocr_cards import CashDirection
from personal_finance.domain.ocr_imports import make_ocr_evidence
from personal_finance.domain.ocr_profiles import UNIVERSAL_PROFILE
from personal_finance.domain.ocr_settings import OcrPreferences, TransferParty
from personal_finance.infrastructure.ocr.generic import GenericCardAdapter, GenericHistoryAdapter
from personal_finance.presentation.main_window import MainWindow
from personal_finance.presentation.ocr_batch import BatchDecisionDialog, OcrBatchDialog
from personal_finance.presentation.ocr_confirmation import OcrConfirmationDialog, OcrOperationDialog
from personal_finance.presentation.ocr_preview import OcrPreviewDialog
from tests.gui.test_ocr_confirmation_gui import choose_expense
from tests.integration.test_generic_ocr_upgrade import schema11 as schema11
from tests.ocr_fixtures import document, png_image
from tests.unit.test_generic_ocr import layout


def history_source(values=("12,50 RUB", "+14,20 RUB")):
    image = png_image()
    doc = layout(
        [
            ("История", 40, 20),
            ("03.01", 40, 80),
            ("Магазин", 40, 150),
            (values[0], 750, 150),
            ("Другой пример", 40, 230),
            (values[1], 750, 230),
        ]
    )
    doc = replace(doc, image_sha256=image.sha256)
    return OcrHistoryPreview(
        LoadedOcrImage("3" * 64, image), GenericHistoryAdapter().parse(doc), UNIVERSAL_PROFILE
    )


def preview_dialog(qtbot):
    dialog = OcrBatchDialog(
        history_source(),
        None,
        [],
        [],
        OcrPreferences(reference_year=2026, confirm_fields=False, review_before_save=False),
    )
    qtbot.addWidget(dialog)
    return dialog


def test_universal_list_without_book_reviews_all_fields_in_memory(qtbot):
    dialog = preview_dialog(qtbot)
    assert dialog.importing is None and "Без записи" in dialog.windowTitle()
    assert dialog.common_account.isHidden()
    assert all(dialog.table.isColumnHidden(i) for i in (3, 4, 5, 6))
    assert dialog.rows[0].amount.text() == "12,50"
    dialog.unknown_flow.setCurrentIndex(dialog.unknown_flow.findData(CashDirection.OUT))
    assert dialog.rows[0].amount.text() == "−12,50"
    assert dialog.rows[1].amount.text() == "+14,20"
    dialog.prepare()
    assert dialog._job is None and dialog.saved_count == 0 and len(dialog.reviewed_cards) == 2
    assert all(c.occurred_on.year == 2026 for c in dialog.reviewed_cards)
    assert "ничего не записано" in dialog.notice.text()
    dialog.rows[0].amount.setText("−15,40")
    assert dialog.reviewed_cards == ()


def test_unknown_direction_is_not_applied_until_explicit_choice(qtbot):
    dialog = preview_dialog(qtbot)
    dialog.prepare()
    assert dialog.reviewed_cards == () and "знаком" in dialog.notice.text()
    dialog.rows[0].kind.setCurrentIndex(dialog.rows[0].kind.findData("income"))
    assert dialog.rows[0].amount.text() == "+12,50"
    dialog.prepare()
    assert len(dialog.reviewed_cards) == 2


def test_bad_date_does_not_discard_other_row_edits(qtbot):
    dialog = preview_dialog(qtbot)
    dialog.unknown_flow.setCurrentIndex(dialog.unknown_flow.findData(CashDirection.OUT))
    dialog.rows[1].occurred_on.setText("31.02.2026")
    dialog.prepare()
    assert not dialog.reviewed_cards and dialog.rows[0].amount.text() == "−12,50"
    assert "Строка 2" in dialog.notice.text()


def test_main_window_legacy_universal_list_stays_without_financial_service(
    qtbot, schema11, monkeypatch
):
    window = MainWindow()
    qtbot.addWidget(window)
    window.activate_session(schema11)
    seen = []

    def inspect(dialog):
        seen.append(dialog.importing)
        dialog.unknown_flow.setCurrentIndex(dialog.unknown_flow.findData(CashDirection.OUT))
        dialog.prepare()
        return QDialog.DialogCode.Rejected

    monkeypatch.setattr(OcrBatchDialog, "exec", inspect)
    window._show_ocr_preview(history_source())
    assert seen == [None] and not schema11.ledger.operations()
    window.lock_book()


def test_generic_single_card_has_no_financial_prepare_button(qtbot):
    image = png_image()
    doc = document(
        ("Вымышленный банк", "Покупка", "-12,50 RUB", "03.01.2026"), image_sha256=image.sha256
    )
    card = GenericCardAdapter().parse(doc)
    preview = OcrCardPreview(LoadedOcrImage("3" * 64, image), card, UNIVERSAL_PROFILE)
    dialog = OcrPreviewDialog(preview, review_defaults=OcrReviewDefaults(confirm_fields=False))
    qtbot.addWidget(dialog)
    assert dialog.prepare_button is None
    dialog.finish.click()
    assert dialog.reviewed is not None
    origin = make_ocr_evidence(preview.source.source_sha256, UNIVERSAL_PROFILE, dialog.reviewed)
    assert origin.data["bank"] == UNIVERSAL_PROFILE
    assert origin.data["adapter"].startswith("generic-")


class Engine:
    def __init__(self, doc):
        self.doc = doc
        self.calls = 0

    def recognize(self, image):
        self.calls += 1
        return replace(self.doc, image_sha256=image.sha256, width=image.width, height=image.height)


def service(doc):
    loaded = LoadedOcrImage("3" * 64, png_image())
    engine = Engine(doc)
    return CardRecognition(
        Ocr(engine),
        lambda _: loaded,
        GenericCardAdapter(),
        history_adapter=GenericHistoryAdapter(),
        bank_name=UNIVERSAL_PROFILE,
        auto_history_hint=GenericHistoryAdapter.has_heading,
    ), engine


def test_auto_selects_list_or_card_without_a_bank_template():
    one, engine = service(document(("Детали операции", "-12 RUB", "03.01.2026")))
    assert isinstance(one.analyze(Path("unused.png")), OcrCardPreview) and engine.calls == 1
    two, engine = service(history_source().history.document)
    result = two.analyze(Path("unused.png"))
    assert (
        isinstance(result, OcrHistoryPreview)
        and len(result.history.cards) == 2
        and engine.calls == 1
    )


def test_explicit_history_button_uses_one_worker(qtbot, session, monkeypatch):
    recognition, engine = service(history_source().history.document)
    window = MainWindow(ocr_recognitions=(recognition,))
    qtbot.addWidget(window)
    monkeypatch.setattr(
        QFileDialog, "getOpenFileNames", lambda *args: (["unused.png", "second.jpg"], "")
    )
    monkeypatch.setattr(OcrBatchDialog, "exec", lambda self: pytest.fail("Вызван отдельный диалог"))
    window.activate_session(session)
    window.show()
    window.ocr_button.click()
    assert window.ocr_input.isVisible() and window.add_operation_button.isVisible()
    window.ocr_input.mode.setCurrentIndex(1)
    window.ocr_input.choose_files()
    assert window.ocr_input.files.count() == 2
    window.ocr_input.recognize.click()
    qtbot.waitUntil(lambda: not window._busy and window._journal_forms.isVisible(), timeout=10000)
    batch = window._journal_forms.stack.currentWidget().widget()
    assert isinstance(batch, OcrBatchDialog) and not batch.isWindow()
    assert len(batch.rows) == 2 and engine.calls == 1
    batch.reject()
    assert window.ocr_input.isVisible() and window.ocr_input.files.count() == 2
    window.lock_book()
    assert window.ocr_input.files.count() == 0 and window._journal_forms.stack.count() == 0


def test_drop_batch_review_cancel_confirm_and_repeat_stay_in_page(qtbot, session, monkeypatch):
    recognition, _ = service(history_source(("-12,50 RUB", "+14,20 RUB")).history.document)
    account = session.catalog.add_account("Синтетический счёт", AccountKind.CASH)
    window = MainWindow(ocr_recognitions=(recognition,))
    qtbot.addWidget(window)
    monkeypatch.setattr(QDialog, "exec", lambda self: pytest.fail("Вызван отдельный диалог"))
    window.activate_session(session)
    window._ocr_preferences = OcrPreferences(
        reference_year=2026, confirm_fields=False, review_before_save=True
    )
    window.show()
    window.ocr_button.click()
    window.ocr_input.mode.setCurrentIndex(1)
    mime = QMimeData()
    mime.setUrls([QUrl.fromLocalFile("synthetic.png"), QUrl("https://example.test/image.png")])
    drop = QDropEvent(
        QPointF(),
        Qt.DropAction.CopyAction,
        mime,
        Qt.MouseButton.LeftButton,
        Qt.KeyboardModifier.NoModifier,
    )
    window.ocr_input.dropEvent(drop)
    assert drop.isAccepted() and window.ocr_input.files.count() == 1

    def current():
        return window._journal_forms.stack.currentWidget().widget()

    def read_batch():
        window.ocr_input.recognize.click()
        qtbot.waitUntil(
            lambda: not window._busy and window._journal_forms.isVisible(), timeout=10000
        )
        batch = current()
        assert isinstance(batch, OcrBatchDialog) and not batch.isWindow()
        batch.common_account.setCurrentIndex(batch.common_account.findData(account.id))
        return batch

    batch = read_batch()
    batch.table.selectRow(0)
    batch.edit_selected()
    card = current()
    assert isinstance(card, OcrPreviewDialog) and not card.isWindow()
    card.reject()
    assert current() is batch

    batch.prepare()
    qtbot.waitUntil(lambda: isinstance(current(), BatchDecisionDialog), timeout=10000)
    decision = current()
    assert not decision.isWindow() and not session.ledger.operations()
    decision.reject()
    assert current() is batch and batch.rows[0].amount.text() and not session.ledger.operations()

    batch.prepare()
    qtbot.waitUntil(lambda: isinstance(current(), BatchDecisionDialog), timeout=10000)
    current()._save()
    qtbot.waitUntil(lambda: batch.saved_count == 2 and not window._busy, timeout=10000)
    assert current() is batch and all(r.saved for r in batch.rows)
    assert (
        next(b.amount.minor for b in session.ledger.balances() if b.account_id == account.id) == 170
    )
    batch.reject()

    repeated = read_batch()
    repeated.prepare()
    qtbot.waitUntil(lambda: isinstance(current(), BatchDecisionDialog), timeout=10000)
    current()._save()
    assert "Выберите" in window._journal_forms.notice.text()
    assert len(session.ledger.operations()) == 2
    session.check_integrity()
    window.lock_book()


def test_clipboard_image_stays_in_memory_and_opens_card(qtbot, qapp, session, monkeypatch):
    recognition, engine = service(document(("Детали операции", "-12 RUB", "03.01.2026")))
    session.catalog.add_account("Синтетическая карта", AccountKind.CASH)
    window = MainWindow(ocr_recognitions=(recognition,))
    qtbot.addWidget(window)
    monkeypatch.setattr(QDialog, "exec", lambda self: pytest.fail("Вызван отдельный диалог"))
    image = QImage.fromData(png_image().png)
    qapp.clipboard().setImage(image)
    try:
        window.activate_session(session)
        window._ocr_preferences = OcrPreferences(confirm_fields=False, review_before_save=True)
        window.show()
        window.ocr_button.click()
        window.ocr_input.paste()
        assert window.ocr_input.files.currentText() == "Изображение из буфера"
        window.ocr_input.recognize.click()
        qtbot.waitUntil(
            lambda: not window._busy and window._journal_forms.isVisible(), timeout=10000
        )
        card = window._journal_forms.stack.currentWidget().widget()
        assert isinstance(card, OcrPreviewDialog) and not card.isWindow()
        assert card.preview.source.source_sha256 == card.preview.source.image.sha256
        assert card.preview.card.amount.value == 1200 and engine.calls == 1
        card._request_financial()
        operation = window._journal_forms.stack.currentWidget().widget()
        assert isinstance(operation, OcrOperationDialog) and not operation.isWindow()
        choose_expense(operation, session.catalog.accounts(), session.catalog.categories())
        operation._validate()

        def current():
            scroll = window._journal_forms.stack.currentWidget()
            return scroll.widget() if scroll is not None else None

        qtbot.waitUntil(lambda: isinstance(current(), OcrConfirmationDialog), timeout=10000)
        confirmation = current()
        assert not confirmation.isWindow() and not session.ledger.operations()
        confirmation.verified.setChecked(True)
        confirmation._validate()
        qtbot.waitUntil(
            lambda: not window._busy and len(session.ledger.operations()) == 1, timeout=10000
        )
        session.check_integrity()
        window.lock_book()
        assert window.ocr_input.files.count() == 0
    finally:
        qapp.clipboard().clear()


def test_readonly_preview_does_not_apply_hidden_financial_exclusion_rules(qtbot):
    dialog = OcrBatchDialog(
        history_source(),
        None,
        [],
        [],
        OcrPreferences(
            confirm_fields=False, transfer_parties=(TransferParty("Магазин", exclude=True),)
        ),
    )
    qtbot.addWidget(dialog)
    assert dialog.rows[0].include.isChecked()
    dialog.unknown_flow.setCurrentIndex(dialog.unknown_flow.findData(CashDirection.OUT))
    dialog.prepare()
    assert len(dialog.reviewed_cards) == 2 and dialog.saved_count == 0


@pytest.mark.parametrize("second", ["Сумма операции: 12,50 RUB", "Сумма операции: 13,50 RUB"])
def test_auto_does_not_turn_repeated_card_amounts_into_a_list(second):
    recognition, engine = service(document(("Платёж", "-12,50 RUB", second, "03.01.2026")))
    result = recognition.analyze(Path("unused.png"))
    assert isinstance(result, OcrCardPreview) and engine.calls == 1
    assert bool(result.card.blocked_reason) == ("13,50" in second)
    if not result.card.blocked_reason:
        assert result.card.amount.value == 1250
