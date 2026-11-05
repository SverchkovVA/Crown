"""Ошибка подготовки ведёт к нужной строке, не изменяя финансовые факты."""

from dataclasses import replace

from PySide6.QtCore import Qt

from personal_finance.domain.errors import ValidationError
from personal_finance.domain.ocr_settings import OcrPreferences
from personal_finance.presentation.ocr_batch import OcrBatchDialog
from tests.gui.test_ocr_batch_gui import dialog_for, source


def test_readonly_long_list_scrolls_to_bad_row_and_preserves_edits(qtbot):
    base = source()
    preview = replace(base, history=replace(base.history, cards=base.history.cards * 30))
    dialog = OcrBatchDialog(
        preview,
        None,
        [],
        [],
        OcrPreferences(reference_year=2026, confirm_fields=False),
    )
    qtbot.addWidget(dialog)
    dialog.resize(1000, 540)
    dialog.show()
    dialog.activateWindow()
    qtbot.waitUntil(dialog.isActiveWindow)
    dialog.rows[0].amount.setText("−14,70")
    dialog.rows[55].amount.setText("123,45")
    dialog.table.setCurrentCell(0, 1)
    dialog.table.scrollToTop()
    dialog.table.horizontalScrollBar().setValue(0)
    dialog.prepare()
    assert dialog.table.currentRow() == 55
    assert dialog.rows[0].amount.text() == "−14,70"
    assert dialog.rows[55].amount.text() == "123,45"
    assert dialog.rows[55].include.isChecked()
    assert dialog._job is None and not dialog.reviewed_cards
    assert "Строка 56" in dialog.notice.text()
    assert dialog.rows[55].state.text() in dialog.selection_note.text()
    qtbot.waitUntil(
        lambda: (
            dialog.table.viewport()
            .rect()
            .contains(dialog.table.visualItemRect(dialog.rows[55].state).center())
        )
    )
    assert dialog.table.hasFocus()


def test_multiple_bad_rows_select_first_keep_confirmation_and_write_nothing(qtbot, session):
    preferences = OcrPreferences(reference_year=2026, confirm_fields=True, review_before_save=False)
    dialog, _ = dialog_for(qtbot, session, preferences)
    dialog.rows[0].amount.setText("123,45")
    dialog.rows[1].occurred_on.setText("31.02.2026")
    dialog.table.setCurrentCell(1, 1)
    dialog.checked.setChecked(True)
    dialog.prepare()
    assert dialog.table.currentRow() == 0
    assert "Не готовы строки: 2." in dialog.notice.text()
    assert "знак" in dialog.rows[0].state.text()
    assert "дату" in dialog.rows[1].state.text()
    assert dialog.checked.isChecked()
    assert all(row.include.isChecked() for row in dialog.rows)
    assert not session.ledger.operations() and dialog._job is None


def test_corrected_row_can_be_saved_once_after_error(qtbot, session):
    dialog, _ = dialog_for(qtbot, session)
    dialog.rows[1].occurred_on.setText("31.02.2026")
    dialog.prepare()
    assert dialog.table.currentRow() == 1
    assert dialog._job is None and not session.ledger.operations()
    dialog.rows[1].occurred_on.setText("24.10.2026")
    qtbot.mouseClick(dialog.save, Qt.MouseButton.LeftButton)
    qtbot.waitUntil(lambda: dialog.saved_count == 2 and dialog._job is None, timeout=15000)
    assert len(session.ledger.operations()) == 2
    assert all(row.saved for row in dialog.rows)
    assert not dialog.save.isEnabled()
    session.check_integrity()


def test_excluded_bad_row_is_not_selected_or_reported(qtbot):
    dialog = OcrBatchDialog(
        source(),
        None,
        [],
        [],
        OcrPreferences(reference_year=2026, confirm_fields=False),
    )
    qtbot.addWidget(dialog)
    dialog.rows[0].amount.setText("123,45")
    dialog.rows[0].include.setChecked(False)
    dialog.table.setCurrentCell(1, 1)
    dialog.prepare()
    assert len(dialog.reviewed_cards) == 1
    assert dialog.table.currentRow() == 1 and not dialog._job
    assert "Проверены поля строк: 1" in dialog.notice.text()


def test_error_tooltip_is_literal_and_source_selection_follows_row(qtbot, session, monkeypatch):
    dialog, _ = dialog_for(qtbot, session)
    dialog.table.setCurrentCell(1, 1)

    def refuse(index):
        raise ValidationError("<учебная ошибка>")

    monkeypatch.setattr(dialog, "_selection", refuse)
    dialog.prepare()
    assert dialog.table.currentRow() == 0
    assert dialog.rows[0].state.text() == "<учебная ошибка>"
    assert "&lt;учебная ошибка&gt;" in dialog.rows[0].state.toolTip()
    assert "<учебная ошибка>" in dialog.selection_note.text()
    assert not session.ledger.operations() and dialog._job is None
