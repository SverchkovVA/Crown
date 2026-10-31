from dataclasses import replace

import pytest
from PySide6.QtCore import QBuffer, QIODevice, QThread
from PySide6.QtGui import QImage, QImageWriter
from PySide6.QtWidgets import QDialog, QFileDialog, QMessageBox

from personal_finance.application.ocr import Ocr
from personal_finance.application.ocr_cards import CardRecognition, OcrCardPreview
from personal_finance.application.ocr_review import OcrReviewDefaults
from personal_finance.domain.errors import ValidationError
from personal_finance.domain.ocr import LoadedOcrImage, OcrImage
from personal_finance.domain.ocr_cards import CashDirection
from personal_finance.infrastructure.ocr.images import load_ocr_image
from personal_finance.infrastructure.ocr.t_bank import TBankCardAdapter
from personal_finance.presentation.main_window import MainWindow
from personal_finance.presentation.ocr_preview import OcrPreviewDialog
from tests.ocr_fixtures import LINES, card, document


def source():
    pixels = QImage(1080, 1400, QImage.Format.Format_RGB32)
    pixels.fill("white")
    buf = QBuffer()
    buf.open(QIODevice.OpenModeFlag.WriteOnly)
    assert QImageWriter(buf, b"PNG").write(pixels)
    return LoadedOcrImage("3" * 64, OcrImage(buf.data().data(), 1080, 1400))


def preview(lines=LINES):
    loaded = source()
    return OcrCardPreview(loaded, card(lines, image_sha256=loaded.image.sha256))


def confirmed(dialog):
    for check in dialog.checks.values():
        check.setChecked(True)


def test_all_required_fields_start_unconfirmed_and_source_is_available(qtbot):
    dialog = OcrPreviewDialog(preview())
    qtbot.addWidget(dialog)
    assert dialog.amount.text() == "1 234,56"
    assert dialog.direction.currentData() == CashDirection.OUT
    assert dialog.occurred_on.text() == "27.10.2026"
    assert not dialog.finish.isEnabled() and not any(c.isChecked() for c in dialog.checks.values())
    assert all(b.isEnabled() for b in dialog.evidence_buttons.values())


def test_unknown_currency_and_year_are_visible_suggestions_without_changing_source(qtbot):
    dialog = OcrPreviewDialog(
        preview((*LINES[:2], "1234,56 Р", "27 октября", *LINES[4:])),
        review_defaults=OcrReviewDefaults(reference_year=2026),
    )
    qtbot.addWidget(dialog)
    assert dialog.currency.currentData() == "RUB" and dialog.direction.currentData() is None
    assert dialog.occurred_on.text() == "27.10.2026" and dialog.local_time.text() == ""
    assert "RUB" in dialog.defaults_note.text() and "2026" in dialog.defaults_note.text()
    dialog.currency.setCurrentIndex(1)
    dialog.direction.setCurrentIndex(2)
    dialog.occurred_on.setText("27.10.2026")
    confirmed(dialog)
    assert dialog.finish.isEnabled()
    dialog._validate()
    assert dialog.result() == QDialog.DialogCode.Accepted
    assert dialog.reviewed.amount.minor == 123456
    assert set(dialog.reviewed.changed_fields) == {"currency", "direction", "occurred_on"}
    assert dialog.preview.card.currency.value is None


@pytest.mark.parametrize("field", ["amount", "currency", "direction", "occurred_on"])
def test_edit_invalidates_previous_confirmation(field, qtbot):
    dialog = OcrPreviewDialog(preview())
    qtbot.addWidget(dialog)
    confirmed(dialog)
    widget = getattr(dialog, field)
    if field in ("currency", "direction"):
        widget.setCurrentIndex(0)
    else:
        widget.setText("1")
    assert not dialog.checks[field].isChecked() and not dialog.finish.isEnabled()


def test_invalid_manual_input_cannot_finish_or_expose_user_value(qtbot, monkeypatch):
    dialog = OcrPreviewDialog(preview())
    qtbot.addWidget(dialog)
    dialog.amount.setText("PRIVATE_Неверная_сумма")
    confirmed(dialog)
    errors = []
    monkeypatch.setattr(QMessageBox, "warning", lambda p, title, text: errors.append(text))
    dialog._validate()
    assert dialog.reviewed is None and dialog.result() != QDialog.DialogCode.Accepted
    assert len(errors) == 1 and "PRIVATE" not in errors[0]


def test_unsupported_history_stays_read_only_even_with_confirmations(qtbot):
    dialog = OcrPreviewDialog(preview((*LINES, "История операций")))
    qtbot.addWidget(dialog)
    confirmed(dialog)
    assert not dialog.finish.isEnabled() and "не поддержан" in dialog.notice.text()


def test_evidence_highlight_keeps_whole_image_and_zoom_can_be_reset(qtbot):
    dialog = OcrPreviewDialog(preview())
    qtbot.addWidget(dialog)
    view = dialog.image_view
    original = view.sceneRect()
    dialog.evidence_buttons["amount"].click()
    assert view.outline.isVisible()
    rect = view.outline.rect()
    assert rect.contains(50, 240) and rect.contains(950, 290)
    view.zoom(1.3)
    view.fit_image()
    assert view.sceneRect() == original and original.width() == 1080
    view.show_evidence(())
    assert not view.outline.isVisible()


def test_recognized_text_is_literal_and_cancel_produces_no_review(qtbot):
    dialog = OcrPreviewDialog(preview((*LINES[:4], "Описание: <b>тест & имя</b>")))
    qtbot.addWidget(dialog)
    assert dialog.description.text() == "<b>тест & имя</b>"
    dialog.reject()
    assert dialog.reviewed is None and dialog.result() == QDialog.DialogCode.Rejected


def test_preview_rejects_wrong_image_before_display(qtbot):
    value = preview()
    with pytest.raises(ValidationError):
        replace(
            value,
            card=replace(value.card, document=replace(value.card.document, image_sha256="f" * 64)),
        )


@pytest.mark.parametrize("with_book", [False, True])
def test_main_window_ocr_runs_in_worker_and_review_runs_after_job_cleanup(
    qtbot, qapp, tmp_path, monkeypatch, session, with_book
):
    loaded = source()
    path = tmp_path / "вымышленная.png"
    path.write_bytes(loaded.image.png)
    observed = []

    class Engine:
        def recognize(self, image):
            assert QThread.currentThread() != qapp.thread()
            return document(image_sha256=image.sha256)

    class Adapter:
        def parse(self, doc):
            assert QThread.currentThread() != qapp.thread()
            return TBankCardAdapter().parse(doc)

    service = CardRecognition(Ocr(Engine()), load_ocr_image, Adapter())
    window = MainWindow(ocr_recognitions=(service,))
    qtbot.addWidget(window)
    if with_book:
        window.activate_session(session)
    before = (session.ledger.operations(include_deleted=True), session.ledger.balances())

    def review(dialog):
        assert QThread.currentThread() == qapp.thread()
        assert not window._busy and not window._jobs and not window._job_callbacks
        confirmed(dialog)
        dialog._validate()
        observed.append(dialog.reviewed)
        return QDialog.DialogCode.Accepted

    monkeypatch.setattr(OcrPreviewDialog, "exec", review)
    monkeypatch.setattr(QFileDialog, "getOpenFileName", lambda *a, **k: (str(path), ""))
    window.review_ocr()
    qtbot.waitUntil(lambda: bool(observed), timeout=5000)
    assert observed[0].amount.minor == 123456
    assert (session.ledger.operations(include_deleted=True), session.ledger.balances()) == before
    assert (window.session is session) == with_book
    assert all(a.isEnabled() for a in window._ocr_actions)
    assert path.read_bytes() == loaded.image.png


def test_cancel_before_loading_and_normal_launch_have_no_ocr_service(qtbot, monkeypatch):
    window = MainWindow()
    qtbot.addWidget(window)
    assert not window._ocr_actions
    window.review_ocr()
    assert not window._jobs
    monkeypatch.setattr(QFileDialog, "getOpenFileName", lambda *a, **k: ("", ""))
    window._ocr_recognitions = (CardRecognition(Ocr(object()), load_ocr_image, TBankCardAdapter()),)
    window.review_ocr()
    assert not window._jobs
