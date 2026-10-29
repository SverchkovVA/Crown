from dataclasses import replace
from pathlib import Path

import pytest
from PySide6.QtWidgets import QDialog, QFileDialog

from personal_finance.application.ocr import Ocr
from personal_finance.application.ocr_cards import (
    CardRecognition,
    OcrCardPreview,
    OcrHistoryPreview,
)
from personal_finance.application.ocr_review import OcrReviewDefaults
from personal_finance.domain.ocr import LoadedOcrImage
from personal_finance.domain.ocr_cards import CashDirection
from personal_finance.domain.ocr_imports import make_ocr_evidence
from personal_finance.domain.ocr_profiles import UNIVERSAL_PROFILE
from personal_finance.domain.ocr_settings import OcrPreferences, TransferParty
from personal_finance.infrastructure.ocr.generic import GenericCardAdapter, GenericHistoryAdapter
from personal_finance.presentation.main_window import MainWindow
from personal_finance.presentation.ocr_batch import OcrBatchDialog
from personal_finance.presentation.ocr_preview import OcrPreviewDialog
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


def test_main_window_universal_list_never_receives_financial_service(qtbot, session, monkeypatch):
    window = MainWindow()
    qtbot.addWidget(window)
    window.activate_session(session)
    seen = []

    def inspect(dialog):
        seen.append(dialog.importing)
        dialog.unknown_flow.setCurrentIndex(dialog.unknown_flow.findData(CashDirection.OUT))
        dialog.prepare()
        return QDialog.DialogCode.Rejected

    monkeypatch.setattr(OcrBatchDialog, "exec", inspect)
    window._show_ocr_preview(history_source())
    assert seen == [None] and not session.ledger.operations()
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


def test_menu_automatic_action_uses_one_worker_and_no_book(qtbot, monkeypatch):
    recognition, engine = service(history_source().history.document)
    window = MainWindow(ocr_recognitions=(recognition,))
    qtbot.addWidget(window)
    seen = []
    monkeypatch.setattr(QFileDialog, "getOpenFileName", lambda *args: ("unused.png", ""))
    monkeypatch.setattr(window, "_show_ocr_preview", lambda result: seen.append(result))
    menu = window.menuBar().actions()[0].menu()
    assert menu.actions()[0].text() == "Распознать изображение…"
    menu.actions()[0].trigger()
    qtbot.waitUntil(lambda: not window._busy and bool(seen), timeout=10000)
    assert isinstance(seen[0], OcrHistoryPreview) and engine.calls == 1
    assert window.session is None


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
