import pytest
from PySide6.QtCore import QThread
from PySide6.QtWidgets import QDialog, QFileDialog

from personal_finance.application.ocr import Ocr
from personal_finance.application.ocr_cards import CardRecognition, OcrHistoryPreview
from personal_finance.domain.ocr import LoadedOcrImage
from personal_finance.infrastructure.ocr.images import load_ocr_image
from personal_finance.infrastructure.ocr.t_bank import TBankCardAdapter
from personal_finance.infrastructure.ocr.t_bank_history import TBankHistoryAdapter
from personal_finance.presentation.main_window import MainWindow
from personal_finance.presentation.ocr_history import OcrHistoryDialog
from personal_finance.presentation.ocr_preview import OcrPreviewDialog
from tests.ocr_fixtures import history_document, png_image


def preview():
    image = png_image()
    source = LoadedOcrImage("3" * 64, image)
    return OcrHistoryPreview(
        source, TBankHistoryAdapter().parse(history_document(image_sha256=image.sha256))
    )


def test_history_review_is_separate_for_each_row_and_cancel_keeps_it_unreviewed(qtbot, monkeypatch):
    value = preview()
    dialog = OcrHistoryDialog(value)
    qtbot.addWidget(dialog)
    assert dialog.model.rowCount() == 2 and dialog.review_button.isEnabled()

    def check(child):
        for flag in child.checks.values():
            flag.setChecked(True)
        child.single.setChecked(True)
        child._validate()
        return QDialog.DialogCode.Accepted

    monkeypatch.setattr(OcrPreviewDialog, "exec", check)
    dialog.review_selected()
    assert set(dialog.reviews) == {0}
    assert dialog.model.item(0, 3).text() == "Проверено"
    assert dialog.model.item(1, 3).text() == "Требует проверки"
    dialog.table.selectRow(1)
    monkeypatch.setattr(OcrPreviewDialog, "exec", lambda child: QDialog.DialogCode.Rejected)
    dialog.review_selected()
    assert set(dialog.reviews) == {0} and value.history.cards[1].currency.value is None


def test_empty_history_has_no_action(qtbot):
    value = preview()
    from dataclasses import replace

    value = replace(
        value, history=replace(value.history, cards=(), blocked_reason="Макет не поддержан")
    )
    dialog = OcrHistoryDialog(value)
    qtbot.addWidget(dialog)
    assert not dialog.review_button.isEnabled()


def test_history_menu_uses_worker_and_cleans_job_before_showing(qtbot, qapp, tmp_path, monkeypatch):
    image = png_image()
    path = tmp_path / "synthetic.png"
    path.write_bytes(image.png)
    seen = []

    class Engine:
        def recognize(self, value):
            assert QThread.currentThread() != qapp.thread()
            return history_document(image_sha256=value.sha256)

    service = CardRecognition(
        Ocr(Engine()), load_ocr_image, TBankCardAdapter(), history_adapter=TBankHistoryAdapter()
    )
    window = MainWindow(ocr_recognitions=(service,))
    qtbot.addWidget(window)
    monkeypatch.setattr(QFileDialog, "getOpenFileName", lambda *a, **k: (str(path), ""))

    def show(dialog):
        assert QThread.currentThread() == qapp.thread()
        assert not window._busy and not window._jobs
        seen.append(len(dialog.preview.history.cards))
        return QDialog.DialogCode.Rejected

    monkeypatch.setattr(OcrHistoryDialog, "exec", show)
    window._ocr_actions[1].trigger()
    qtbot.waitUntil(lambda: bool(seen), timeout=5000)
    assert seen == [2] and window.session is None and window._ocr_actions[1].isEnabled()


def test_selected_row_note_and_source_follow_selection_and_clear(qtbot, monkeypatch):
    dialog = OcrHistoryDialog(preview())
    qtbot.addWidget(dialog)
    assert dialog.selection_note.text() == "Выбрана строка 1 из 2"
    first = dialog.image.outline.rect()
    dialog.table.selectRow(1)
    assert dialog.selection_note.text() == "Выбрана строка 2 из 2"
    assert dialog.image.outline.rect() != first and dialog.review_button.isEnabled()
    dialog.table.clearSelection()
    assert dialog.selection_note.text() == "Строка не выбрана"
    assert not dialog.image.outline.isVisible() and not dialog.review_button.isEnabled()
    # currentIndex остаётся прежним, но не разрешает проверку снятого выбора.
    assert dialog.table.currentIndex().row() == 1
    monkeypatch.setattr(OcrPreviewDialog, "exec", lambda child: pytest.fail("Нет выбранной строки"))
    dialog.review_selected()
    assert not dialog.reviews


def test_selection_is_visible_after_focus_moves_to_review_button(qtbot):
    from PySide6.QtCore import Qt
    from PySide6.QtGui import QColor

    from personal_finance.presentation.main_window import STYLE

    dialog = OcrHistoryDialog(preview())
    dialog.setStyleSheet(STYLE)
    qtbot.addWidget(dialog)
    dialog.show()
    dialog.table.selectRow(1)
    dialog.review_button.setFocus()
    qtbot.waitUntil(lambda: dialog.review_button.hasFocus())
    rectangle = dialog.table.visualRect(dialog.model.index(1, 0))
    point = rectangle.bottomRight()
    point.setX(point.x() - 5)
    point.setY(point.y() - 5)
    pixels = dialog.table.viewport().grab().toImage()
    assert pixels.pixelColor(point) == QColor("#17694f")
    dialog.table.setFocus()
    qtbot.keyClick(dialog.table, Qt.Key.Key_Up)
    assert dialog.selection_note.text() == "Выбрана строка 1 из 2"
