"""Навигация и оформление проверяются на вымышленных данных."""

from PySide6.QtCore import QMimeData, QPoint, QPointF, Qt, QUrl
from PySide6.QtGui import QDragEnterEvent, QDropEvent, QPalette
from PySide6.QtWidgets import QDialog, QDialogButtonBox, QLineEdit, QScrollArea, QVBoxLayout

from personal_finance.presentation.form_layout import ActionFooter
from personal_finance.presentation.inline_forms import present_form
from personal_finance.presentation.main_window import MainWindow
from personal_finance.presentation.ocr_input import OcrInputPanel
from personal_finance.presentation.ocr_preview import OcrPreviewDialog
from personal_finance.presentation.theme import ThemeChoice
from tests.gui.test_ocr_preview import preview


def window_for(qtbot, session):
    window = MainWindow()
    qtbot.addWidget(window)
    window.activate_session(session)
    window.show()
    return window


def form(parent, title):
    value = QDialog(parent)
    value.setWindowTitle(title)
    layout = QVBoxLayout(value)
    value.field = QLineEdit(title)
    layout.addWidget(value.field)
    box = QDialogButtonBox(QDialogButtonBox.StandardButton.Cancel)
    box.rejected.connect(value.reject)
    layout.addWidget(box)
    return value


def test_mouse_side_buttons_and_alt_keys_navigate_sections(qtbot, session):
    window = window_for(qtbot, session)
    before = session.ledger.operations(), session.ledger.balances()
    window.navigation.setCurrentRow(3)
    window.navigation.setCurrentRow(2)
    qtbot.mouseClick(window._report_forms.header, Qt.MouseButton.BackButton)
    assert window.pages.currentIndex() == 3
    qtbot.mouseClick(window._account_forms.header, Qt.MouseButton.ForwardButton)
    assert window.pages.currentIndex() == 2
    qtbot.keyClick(window, Qt.Key.Key_Left, Qt.KeyboardModifier.AltModifier)
    assert window.pages.currentIndex() == 3
    qtbot.keyClick(window, Qt.Key.Key_Forward)
    assert window.pages.currentIndex() == 2
    assert (session.ledger.operations(), session.ledger.balances()) == before
    window.lock_book()
    assert not window._welcome_forms.header.isVisible()
    assert not window._history.entries


def test_back_forward_preserve_same_form_without_accept_or_reject(qtbot, session):
    window = window_for(qtbot, session)
    window.navigation.setCurrentRow(0)
    panel = window._journal_forms
    first, second = form(window, "Условия"), form(window, "Результат")
    finished = []
    present_form(first, panel, lambda result: finished.append(("first", result)))
    first.field.setText("123,45")
    present_form(second, panel, lambda result: finished.append(("second", result)))
    panel.back.click()
    assert panel.stack.currentWidget().widget() is first
    assert first.field.text() == "123,45" and finished == []
    panel.forward.click()
    assert panel.stack.currentWidget().widget() is second and finished == []
    qtbot.mouseClick(second.field, Qt.MouseButton.BackButton)
    assert panel.stack.currentWidget().widget() is first
    third = form(window, "Новый расчёт")
    present_form(third, panel)
    assert panel.stack.count() == 2 and finished == []
    assert not panel.forward.isEnabled()
    window.lock_book()


def test_ocr_actions_stay_visible_while_content_scrolls(qtbot, session):
    window = window_for(qtbot, session)
    window.navigation.setCurrentRow(2)
    window.resize(1040, 700)
    dialog = OcrPreviewDialog(preview(), window, allow_financial_prepare=True)
    present_form(dialog, window._report_forms)
    qtbot.waitUntil(lambda: dialog.isVisible())
    qtbot.wait(30)
    footer = dialog.findChild(ActionFooter)
    assert footer is not None and footer.isAncestorOf(dialog.defaults_note)
    assert footer.isAncestorOf(dialog.notice)
    for button in (dialog.finish, dialog.prepare_button):
        rect = button.rect()
        top = button.mapTo(window, rect.topLeft())
        bottom = button.mapTo(window, rect.bottomRight())
        assert 0 <= top.y() < bottom.y() < window.height()
        assert 0 <= top.x() < bottom.x() < window.width()
        owner = button.parentWidget()
        while owner is not None and owner is not window:
            origin = button.mapTo(owner, rect.topLeft())
            end = button.mapTo(owner, rect.bottomRight())
            assert owner.rect().contains(origin) and owner.rect().contains(end), type(
                owner
            ).__name__
            owner = owner.parentWidget()
    y = dialog.finish.mapTo(window, QPoint(0, 0)).y()
    content = dialog.findChild(QScrollArea, "ocrScroll")
    assert content is not None
    content.verticalScrollBar().setValue(content.verticalScrollBar().maximum())
    assert dialog.finish.mapTo(window, QPoint(0, 0)).y() == y
    assert not dialog.isWindow()
    window.lock_book()


def test_theme_switch_preserves_fields_and_persists_choice(qtbot, session):
    window = window_for(qtbot, session)
    window.navigation.setCurrentRow(2)
    dialog = form(window, "Условия")
    present_form(dialog, window._report_forms)
    dialog.field.setText("−1 234,56 RUB")
    before = session.ledger.operations(), session.ledger.balances()
    window._themes.set_choice(ThemeChoice.DARK)
    assert window.property("darkTheme") is True
    assert window.palette().color(QPalette.ColorRole.Text).lightness() > 180
    assert dialog.field.text() == "−1 234,56 RUB"
    assert window._themes.preferences.load() is ThemeChoice.DARK
    window._themes.set_choice(ThemeChoice.LIGHT)
    assert window.property("darkTheme") is False
    assert dialog.field.text() == "−1 234,56 RUB"
    assert (session.ledger.operations(), session.ledger.balances()) == before
    window.lock_book()


def test_visible_drop_zone_accepts_local_image_and_reports_selection(qtbot, tmp_path):
    panel = OcrInputPanel(True)
    qtbot.addWidget(panel)
    panel.show()
    image = tmp_path / "вымышленный снимок.png"
    data = QMimeData()
    data.setUrls([QUrl.fromLocalFile(str(image))])
    zone = panel.drop_zone
    assert zone.isVisible() and "сюда" in zone.title.text()
    event = QDragEnterEvent(
        QPoint(4, 4),
        Qt.DropAction.CopyAction,
        data,
        Qt.MouseButton.LeftButton,
        Qt.KeyboardModifier.NoModifier,
    )
    zone.dragEnterEvent(event)
    assert event.isAccepted() and zone.property("dragActive") is True
    drop = QDropEvent(
        QPointF(4, 4),
        Qt.DropAction.CopyAction,
        data,
        Qt.MouseButton.LeftButton,
        Qt.KeyboardModifier.NoModifier,
    )
    zone.dropEvent(drop)
    assert drop.isAccepted() and zone.property("dragActive") is False
    assert panel.files.currentData() == image and panel.recognize.isEnabled()
    assert "1" in panel.notice.text()
