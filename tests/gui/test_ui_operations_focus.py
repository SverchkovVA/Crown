"""Пользовательские действия и геометрия компактного журнала и OCR."""

from dataclasses import replace

import pytest
from PySide6.QtCore import QPoint, Qt
from PySide6.QtWidgets import QScrollArea

from personal_finance.application.ocr_review import OcrReviewDefaults
from personal_finance.domain.ledger import OperationDraft, OperationKind
from personal_finance.domain.models import AccountKind, CategoryKind, Money
from personal_finance.domain.ocr_settings import OcrPreferences
from personal_finance.presentation.inline_forms import present_form
from personal_finance.presentation.main_window import MainWindow
from personal_finance.presentation.ocr_batch import OcrBatchDialog
from personal_finance.presentation.ocr_preview import OcrPreviewDialog
from tests.gui.test_ocr_batch_gui import source
from tests.gui.test_ocr_preview import preview


def test_one_click_checks_fields_without_saving_and_edit_requires_new_check(qtbot):
    dialog = OcrPreviewDialog(preview(), allow_financial_prepare=True)
    qtbot.addWidget(dialog)
    dialog.show()
    qtbot.mouseClick(dialog.check_all, Qt.MouseButton.LeftButton)
    assert all(check.isChecked() for check in dialog.checks.values())
    assert dialog.finish.isEnabled() and dialog.prepare_button.isEnabled()
    assert dialog.reviewed is None and not dialog.financial_requested
    dialog.amount.setText("40,50")
    assert not dialog.checks["amount"].isChecked() and not dialog.finish.isEnabled()
    qtbot.mouseClick(dialog.check_all, Qt.MouseButton.LeftButton)
    assert dialog.finish.isEnabled()
    assert dialog.preview.card.amount.value == 123456


@pytest.mark.parametrize("invalid", ["amount", "date", "clipped"])
def test_collective_check_does_not_bypass_validation_or_clipped_source(qtbot, invalid):
    original = preview()
    if invalid == "clipped":
        original = replace(
            original, card=replace(original.card, blocked_reason="Нужен полный снимок")
        )
    dialog = OcrPreviewDialog(original, allow_financial_prepare=True)
    qtbot.addWidget(dialog)
    if invalid == "amount":
        dialog.amount.setText("ошибка")
    elif invalid == "date":
        dialog.occurred_on.setText("31.02.2026")
    dialog.check_all.click()
    assert not dialog.finish.isEnabled() and not dialog.prepare_button.isEnabled()
    assert dialog.reviewed is None


def test_confirmation_off_has_no_redundant_collective_button(qtbot):
    dialog = OcrPreviewDialog(preview(), review_defaults=OcrReviewDefaults(confirm_fields=False))
    qtbot.addWidget(dialog)
    dialog.show()
    assert dialog.check_all.isHidden() and not dialog.checks
    assert dialog.finish.isEnabled()


@pytest.mark.parametrize("width,height", [(1160, 760), (920, 620)])
@pytest.mark.parametrize("empty", [False, True], ids=["rows", "empty"])
def test_embedded_ocr_uses_only_real_content_scroll_and_keeps_actions_visible(
    qtbot, session, width, height, empty
):
    window = MainWindow()
    qtbot.addWidget(window)
    window._fit_on_first_show = False
    window.resize(width, height)
    window.activate_session(session)
    window.show()
    value = source()
    if empty:
        value = replace(value, history=replace(value.history, cards=()))
    dialog = OcrBatchDialog(
        value,
        None,
        [],
        [],
        OcrPreferences(reference_year=2026),
        parent=window,
        panel=window._journal_forms,
    )
    present_form(dialog, window._journal_forms)
    qtbot.wait(30)
    assert dialog.findChild(QScrollArea, "inlineContent") is None
    assert dialog.empty_state.isVisible() == empty
    assert dialog.table.isVisible() != empty
    for target in (dialog.save, dialog.checked):
        point = target.rect().center()
        owner = target
        while owner is not None:
            assert owner.rect().contains(point), type(owner).__name__
            point = owner.mapToParent(point)
            owner = owner.parentWidget()


def test_journal_reveals_filters_and_selected_actions_without_changing_query(qtbot, session):
    from datetime import date

    account = session.catalog.add_account("Учебный", AccountKind.CASH)
    category = session.catalog.add_category("Учебные покупки", CategoryKind.EXPENSE)
    session.ledger.add(
        OperationDraft(
            OperationKind.EXPENSE, date(2026, 10, 3), account.id, Money(500), category.id
        )
    )
    window = MainWindow()
    qtbot.addWidget(window)
    window._fit_on_first_show = False
    window.resize(1160, 760)
    window.activate_session(session)
    window.show()
    original = window._active_query
    qtbot.wait(20)
    assert not window.journal_filters.isVisible()
    assert not window.journal_selection_tools.isVisible()
    assert window.operation_table.height() > window.height() // 2
    qtbot.mouseClick(window.journal_filters_toggle, Qt.MouseButton.LeftButton)
    assert window.journal_filters.isVisible() and window.filter_button.isVisible()
    qtbot.mouseClick(window.journal_filters_toggle, Qt.MouseButton.LeftButton)
    assert window._active_query == original
    qtbot.wait(20)
    table_top = window.operation_table.viewport().mapTo(window, QPoint()).y()
    window.operation_table.selectRow(0)
    qtbot.wait(20)
    assert window.operation_table.viewport().mapTo(window, QPoint()).y() == table_top
    assert window.journal_selection_tools.isVisible()
    assert window.edit_operation_button.isVisible() and window.refund_button.isVisible()
    window.operation_table.clearSelection()
    assert not window.journal_selection_tools.isVisible()


def test_more_menu_keeps_import_export_availability_and_invokes_original_action(qtbot):
    window = MainWindow()
    qtbot.addWidget(window)
    window._update_journal_menu()
    assert all(
        action.isEnabled() == button.isEnabled() for action, button in window.journal_file_actions
    )
    window.import_button.setEnabled(False)
    window._update_journal_menu()
    assert not window.journal_file_actions[0][0].isEnabled()
    seen = []
    window.import_button.setEnabled(True)
    window.import_button.clicked.connect(lambda: seen.append("import"))
    window._update_journal_menu()
    action, source_button = window.journal_file_actions[0]
    assert action.isEnabled() and source_button is window.import_button
    action.trigger()
    assert seen == ["import"]


@pytest.mark.parametrize("width,height", [(1160, 760), (920, 620)])
def test_collective_card_check_stays_in_footer_while_fields_scroll(qtbot, session, width, height):
    from personal_finance.presentation.form_layout import ActionFooter

    window = MainWindow()
    qtbot.addWidget(window)
    window._fit_on_first_show = False
    window.resize(width, height)
    window.activate_session(session)
    window.show()
    dialog = OcrPreviewDialog(preview(), window, allow_financial_prepare=True)
    present_form(dialog, window._journal_forms)
    qtbot.wait(30)
    footer = dialog.findChild(ActionFooter)
    assert footer is not None and footer.isAncestorOf(dialog.check_all)
    fields_scroll = dialog.findChild(QScrollArea, "ocrScroll")
    assert fields_scroll is not None
    fields_scroll.verticalScrollBar().setValue(fields_scroll.verticalScrollBar().maximum())
    qtbot.mouseClick(dialog.check_all, Qt.MouseButton.LeftButton)
    assert dialog.prepare_button.isEnabled() and dialog.finish.isEnabled()
    for target in (dialog.check_all, dialog.finish, dialog.prepare_button):
        point = target.rect().center()
        owner = target
        while owner is not None:
            assert owner.rect().contains(point), type(owner).__name__
            point = owner.mapToParent(point)
            owner = owner.parentWidget()
