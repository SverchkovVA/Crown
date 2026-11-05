from dataclasses import replace

from PySide6.QtCore import QThread
from PySide6.QtWidgets import QDialog, QFileDialog

from personal_finance.application.ocr import Ocr
from personal_finance.application.ocr_cards import (
    CardRecognition,
    OcrCardPreview,
    OcrHistoryPreview,
)
from personal_finance.domain.ocr import LoadedOcrImage
from personal_finance.domain.ocr_profiles import UNIVERSAL_PROFILE
from personal_finance.infrastructure.ocr.generic import GenericCardAdapter, GenericHistoryAdapter
from personal_finance.infrastructure.ocr.images import load_ocr_image
from personal_finance.infrastructure.ocr.t_bank import TBankCardAdapter
from personal_finance.infrastructure.ocr.vtb import VtbCardAdapter
from personal_finance.infrastructure.ocr.vtb_history import VtbHistoryAdapter
from personal_finance.presentation.main_window import MainWindow
from personal_finance.presentation.ocr_history import OcrHistoryDialog
from personal_finance.presentation.ocr_preview import OcrPreviewDialog
from tests.ocr_fixtures import VTB_CARD as CARD
from tests.ocr_fixtures import document, png_image
from tests.ocr_fixtures import vtb_history_document as history


def test_flat_menu_prefers_universal_profile_and_disables_while_working(
    qtbot, qapp, tmp_path, monkeypatch
):
    image = png_image()
    path = tmp_path / "fiction.png"
    path.write_bytes(image.png)
    seen = []

    class Engine:
        def recognize(self, value):
            assert QThread.currentThread() != qapp.thread()
            assert all(not a.isEnabled() for a in window._ocr_actions)
            return document(CARD, image_sha256=value.sha256)

    ocr = Ocr(Engine())
    t = CardRecognition(ocr, load_ocr_image, TBankCardAdapter())
    v = CardRecognition(
        ocr, load_ocr_image, VtbCardAdapter(), history_adapter=VtbHistoryAdapter(), bank_name="ВТБ"
    )
    universal = CardRecognition(
        ocr,
        load_ocr_image,
        GenericCardAdapter(),
        history_adapter=GenericHistoryAdapter(),
        bank_name=UNIVERSAL_PROFILE,
    )
    window = MainWindow(ocr_recognitions=(t, v, universal))
    qtbot.addWidget(window)
    assert [a.text() for a in window._ocr_actions] == ["Карточка операции", "История операций"]
    titles = []
    monkeypatch.setattr(
        QFileDialog,
        "getOpenFileName",
        lambda parent, title, *args: (titles.append(title) or str(path), ""),
    )

    def show(dialog):
        assert dialog.preview.bank_name == UNIVERSAL_PROFILE
        assert dialog.preview.card.adapter == "generic-card-v6"
        assert QThread.currentThread() == qapp.thread() and not window._busy
        seen.append(True)
        return QDialog.DialogCode.Rejected

    monkeypatch.setattr(OcrPreviewDialog, "exec", show)
    window._ocr_actions[0].trigger()
    qtbot.waitUntil(lambda: bool(seen), timeout=5000)
    assert UNIVERSAL_PROFILE in titles[0] and all(a.isEnabled() for a in window._ocr_actions)
    assert window.session is None and path.read_bytes() == image.png


def test_vtb_bank_name_reaches_history_and_its_child_review(qtbot, monkeypatch):
    image = png_image()
    loaded = LoadedOcrImage("3" * 64, image)
    draft = VtbHistoryAdapter().parse(replace(history(), image_sha256=image.sha256))
    dialog = OcrHistoryDialog(OcrHistoryPreview(loaded, draft, "ВТБ"))
    qtbot.addWidget(dialog)
    assert "ВТБ" in dialog.windowTitle()
    seen = []

    def show(child):
        assert child.preview.bank_name == "ВТБ" and "ВТБ" in child.windowTitle()
        assert child.preview.card.document is draft.document
        seen.append(True)
        return QDialog.DialogCode.Rejected

    monkeypatch.setattr(OcrPreviewDialog, "exec", show)
    dialog.review_selected()
    assert seen == [True] and not dialog.reviews
    single = OcrPreviewDialog(OcrCardPreview(loaded, draft.cards[0], "ВТБ"))
    qtbot.addWidget(single)
    assert not single.finish.isEnabled()
