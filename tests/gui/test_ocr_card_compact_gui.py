"""Компактная карточка сохраняет источники и видимые ограничения."""

from dataclasses import replace

import pytest
from PySide6.QtWidgets import QScrollArea

from personal_finance.presentation.inline_forms import present_form
from personal_finance.presentation.main_window import MainWindow
from personal_finance.presentation.ocr_preview import OcrPreviewDialog
from tests.gui.test_ocr_preview import preview


@pytest.mark.parametrize("size", [(1160, 760), (920, 620)])
def test_compact_card_keeps_fields_and_actions_inside_window(qtbot, session, size):
    window = MainWindow()
    qtbot.addWidget(window)
    window._fit_on_first_show = False
    window.resize(*size)
    window.activate_session(session)
    window.show()
    dialog = OcrPreviewDialog(preview(), window, allow_financial_prepare=True)
    present_form(dialog, window._journal_forms)
    qtbot.wait(30)
    assert dialog.info_body.isHidden()
    scroll = dialog.findChild(QScrollArea, "ocrScroll")
    assert scroll is not None
    if size[0] == 1160:
        assert scroll.verticalScrollBar().maximum() == 0
    for target in (dialog.check_all, dialog.finish, dialog.prepare_button, dialog.details_toggle):
        for point in (target.rect().topLeft(), target.rect().bottomRight()):
            owner = target
            while owner is not None:
                assert owner.rect().contains(point), type(owner).__name__
                point = owner.mapToParent(point)
                owner = owner.parentWidget()
    dialog.check_all.click()
    before = dialog._checked_fields(), dialog.amount.text(), dialog.occurred_on.text()
    dialog.details_toggle.click()
    qtbot.wait(20)
    assert dialog.info_body.isVisible()
    assert scroll.isAncestorOf(dialog.defaults_note)
    assert scroll.isAncestorOf(dialog.notice)
    assert dialog.validation_note.isVisible()
    assert (dialog._checked_fields(), dialog.amount.text(), dialog.occurred_on.text()) == before
    assert dialog.prepare_button.isEnabled()
    dialog.details_toggle.click()
    assert dialog.info_body.isHidden()


def test_evidence_stays_available_and_identifiable_for_keyboard_access(qtbot):
    dialog = OcrPreviewDialog(preview())
    qtbot.addWidget(dialog)
    dialog.show()
    for name, button in dialog.evidence_buttons.items():
        assert button.accessibleName().startswith("Показать источник:")
        assert button.toolTip() == button.accessibleDescription()
        assert "Источник:" in button.toolTip()
        assert getattr(dialog, name).accessibleName()
    dialog.evidence_buttons["amount"].click()
    assert dialog.image_view.outline.isVisible()
    assert dialog.image_view.document == dialog.preview.card.document


def test_clipped_card_block_is_visible_when_details_are_collapsed(qtbot):
    value = preview()
    reason = "Строка суммы у края изображения. Нужен полный снимок."
    value = replace(value, card=replace(value.card, blocked_reason=reason))
    dialog = OcrPreviewDialog(value, allow_financial_prepare=True)
    qtbot.addWidget(dialog)
    dialog.show()
    dialog.check_all.click()
    assert dialog.info_body.isHidden() and dialog.validation_note.isVisible()
    assert dialog.validation_note.text() == reason
    assert not dialog.prepare_button.isEnabled()
