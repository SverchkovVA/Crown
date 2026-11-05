"""Размещение навигации и пакетной проверки — только синтетические данные."""

from PySide6.QtCore import QPoint, QPointF, Qt
from PySide6.QtGui import QWheelEvent
from PySide6.QtWidgets import QApplication, QComboBox, QDialogButtonBox

from personal_finance.domain.models import AccountKind
from personal_finance.domain.ocr_settings import OcrPreferences
from personal_finance.presentation.form_layout import ActionFooter
from personal_finance.presentation.inline_forms import present_form
from personal_finance.presentation.main_window import MainWindow
from personal_finance.presentation.ocr_batch import OcrBatchDialog
from personal_finance.presentation.ocr_preview import OcrPreviewDialog
from personal_finance.presentation.ocr_table import OcrReviewTable
from tests.gui.test_ocr_batch_gui import source
from tests.gui.test_ocr_preview import preview


def test_header_above_sidebar_and_active_title_without_overlay(qtbot, session):
    window = MainWindow()
    qtbot.addWidget(window)
    window.activate_session(session)
    window.resize(1160, 760)
    window.show()
    for index in range(window.navigation.count()):
        window.navigation.setCurrentRow(index)
        panel = window._panel_for_section(index)
        assert panel.heading.text() == window.navigation.item(index).text()
        assert panel.back.mapTo(window, QPoint()).x() < window.sidebar.width()
        assert panel.header.mapTo(window, panel.header.rect().bottomLeft()).y() < (
            window.sidebar.mapTo(window, QPoint()).y()
        )
        assert panel.heading.mapTo(window, QPoint()).x() >= window.sidebar.width()
        assert all(
            not p.header.isVisible()
            for p in window._action_panels
            if p is not panel and p.header_managed
        )
    window.navigation.setCurrentRow(0)
    dialog = OcrPreviewDialog(preview(), window)
    present_form(dialog, window._journal_forms)
    assert window._journal_forms.heading.text() == "Карточка операции"
    close = dialog.findChild(QDialogButtonBox).button(QDialogButtonBox.StandardButton.Cancel)
    assert close.isHidden()
    window._journal_forms.back.click()
    assert window._journal_forms.heading.text() == "Операции"
    window._journal_forms.forward.click()
    assert window._journal_forms.heading.text() == "Карточка операции"
    window.lock_book()


def test_batch_add_and_confirmation_remain_visible_at_bottom(qtbot, session):
    session.catalog.add_account("Вымышленный счёт", AccountKind.CASH)
    window = MainWindow()
    qtbot.addWidget(window)
    window.activate_session(session)
    window.resize(1040, 700)
    window.show()
    dialog = OcrBatchDialog(
        source(),
        session.ocr_importing,
        session.catalog.accounts(),
        session.catalog.categories(),
        OcrPreferences(reference_year=2026),
        parent=window,
        panel=window._journal_forms,
    )
    present_form(dialog, window._journal_forms)
    qtbot.wait(40)
    assert dialog.windowTitle() == "История операций"
    footer = dialog.findChild(ActionFooter)
    assert footer is not None and footer.isAncestorOf(dialog.save)
    assert footer.isAncestorOf(dialog.checked)
    assert dialog.close_button.isHidden()
    assert dialog.table.rowHeight(0) >= dialog.rows[0].occurred_on.minimumHeight()
    assert dialog.table.rowHeight(0) >= dialog.rows[0].occurred_on.sizeHint().height()
    assert dialog.save.text() == "Добавить операции (2)"
    assert dialog.save.mapTo(window, QPoint()).x() < dialog.checked.mapTo(window, QPoint()).x()
    for button in (dialog.save, dialog.checked):
        owner = button.parentWidget()
        while owner is not None and owner is not window:
            assert owner.rect().contains(button.mapTo(owner, button.rect().topLeft()))
            assert owner.rect().contains(button.mapTo(owner, button.rect().bottomRight()))
            owner = owner.parentWidget()
    before = dialog.save.mapTo(window, QPoint()).y()
    dialog.table.horizontalScrollBar().setValue(dialog.table.horizontalScrollBar().maximum())
    assert dialog.save.mapTo(window, QPoint()).y() == before
    assert session.ledger.operations() == []
    window.lock_book()


def wheel(widget, *, shift=False):
    event = QWheelEvent(
        QPointF(5, 5),
        QPointF(widget.mapToGlobal(QPoint(5, 5))),
        QPoint(),
        QPoint(0, -120),
        Qt.MouseButton.NoButton,
        Qt.KeyboardModifier.ShiftModifier if shift else Qt.KeyboardModifier.NoModifier,
        Qt.ScrollPhase.NoScrollPhase,
        False,
    )
    QApplication.sendEvent(widget, event)


def test_wheel_scrolls_table_without_changing_combo_and_shift_scrolls_rows(qtbot):
    table = OcrReviewTable(80, 3)
    qtbot.addWidget(table)
    combo = QComboBox()
    combo.addItems(["Расход", "Доход", "Свой перевод"])
    table.setCellWidget(0, 0, combo)
    table.configure_columns((320, 320, 320))
    table.resize(500, 300)
    table.show()
    qtbot.wait(20)
    wheel(combo)
    assert table.horizontalScrollBar().value() > 0
    assert combo.currentIndex() == 0
    horizontal = table.horizontalScrollBar().value()
    wheel(table.viewport(), shift=True)
    assert table.verticalScrollBar().value() > 0
    assert table.horizontalScrollBar().value() == horizontal


def test_columns_fill_wide_view_without_one_giant_last_column(qtbot):
    table = OcrReviewTable(2, 3)
    qtbot.addWidget(table)
    table.configure_columns((180, 180, 180))
    table.resize(1100, 400)
    table.show()
    qtbot.wait(20)
    widths = [table.columnWidth(i) for i in range(3)]
    assert max(widths) - min(widths) <= 1
    assert sum(widths) == table.viewport().width()
    table.resize(450, 400)
    qtbot.wait(20)
    assert all(table.columnWidth(i) >= 180 for i in range(3))
    assert table.horizontalScrollBar().maximum() > 0
