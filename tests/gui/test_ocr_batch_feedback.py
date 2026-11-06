"""Ошибка подготовки ведёт к нужной строке, не изменяя финансовые факты."""

from dataclasses import replace

import pytest
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


def test_inline_bad_row_is_visible_through_every_scroll_viewport(qtbot, session):
    from personal_finance.domain.models import AccountKind
    from personal_finance.presentation.inline_forms import present_form
    from personal_finance.presentation.main_window import MainWindow

    account = session.catalog.add_account("Учебный счёт длинного списка", AccountKind.CASH)
    window = MainWindow()
    qtbot.addWidget(window)
    window.activate_session(session)
    window.resize(920, 440)
    window.show()
    base = source()
    preview = replace(base, history=replace(base.history, cards=base.history.cards * 30))
    dialog = OcrBatchDialog(
        preview,
        session.ocr_importing,
        session.catalog.accounts(),
        session.catalog.categories(),
        OcrPreferences(reference_year=2026, confirm_fields=False, review_before_save=False),
        parent=window,
        panel=window._journal_forms,
    )
    present_form(dialog, window._journal_forms)
    dialog.common_account.setCurrentIndex(dialog.common_account.findData(account.id))
    dialog.rows[55].occurred_on.setText("31.02.2026")
    qtbot.wait(30)
    dialog.prepare()

    def visible_in_all_ancestors():
        point = dialog.table.visualItemRect(dialog.rows[55].state).center()
        widget = dialog.table.viewport()
        while widget is not None:
            if not widget.rect().contains(point):
                return False
            point = widget.mapToParent(point)
            widget = widget.parentWidget()
        return True

    assert dialog.table.currentRow() == 55
    qtbot.waitUntil(visible_in_all_ancestors)
    assert not session.ledger.operations() and dialog._job is None
    window.lock_book()


def test_new_selection_is_not_overridden_by_deferred_problem_scroll(qtbot):
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
    dialog.show()
    qtbot.wait(20)
    dialog.rows[55].amount.setText("123,45")
    dialog.prepare()
    dialog.table.setCurrentCell(0, 1)
    dialog.table.scrollToTop()
    qtbot.wait(20)
    assert dialog.table.currentRow() == 0
    assert dialog.table.verticalScrollBar().value() == 0
    assert dialog.rows[55].amount.text() == "123,45"


@pytest.mark.parametrize("action", ["back", "lock"])
def test_navigation_before_deferred_reveal_keeps_new_page(qtbot, session, action):
    from personal_finance.presentation.inline_forms import present_form
    from personal_finance.presentation.main_window import MainWindow

    window = MainWindow()
    qtbot.addWidget(window)
    window.activate_session(session)
    window.show()
    dialog = OcrBatchDialog(
        source(),
        None,
        [],
        [],
        OcrPreferences(reference_year=2026, confirm_fields=False),
        parent=window,
        panel=window._journal_forms,
    )
    present_form(dialog, window._journal_forms)
    dialog.rows[1].amount.setText("123,45")
    dialog.prepare()
    if action == "back":
        window._journal_forms.back.click()
    else:
        window.lock_book()
    qtbot.wait(20)
    if action == "back":
        assert window._journal_forms.heading.text() == "Операции"
        assert not dialog.isVisible()
        window.lock_book()
    else:
        assert window.session is None and window.root_stack.currentIndex() == 0
        assert not window._history.entries
