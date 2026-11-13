"""Пользовательская навигация и границы экрана закрытой книги."""

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QApplication,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QLineEdit,
    QPushButton,
)

from personal_finance.domain.models import AccountKind
from personal_finance.presentation.backup_contents import BackupContentsDialog
from personal_finance.presentation.dialogs import PasswordDialog
from personal_finance.presentation.ledger_dialogs import OperationDialog
from personal_finance.presentation.main_window import MainWindow

ORDER = [
    "Операции",
    "Бонусы",
    "Отчёты",
    "Счета",
    "Теги и фильтры",
    "Категории",
    "Периоды",
    "Правила категорий",
    "Настройки",
]


def test_closed_book_has_only_welcome_and_no_top_commands(qtbot):
    window = MainWindow()
    qtbot.addWidget(window)
    window.show()
    assert window.menuBar().isHidden() and not window.menuBar().actions()
    assert window.root_stack.currentIndex() == 0
    assert not window.navigation.isVisible()
    assert not window.close_book_button.isVisible()
    assert not any(b.isVisible() for b in window.report_buttons)


def test_initial_window_fits_the_available_screen(qtbot):
    window = MainWindow()
    qtbot.addWidget(window)
    available = window.screen().availableGeometry()
    window.resize(available.width() + 120, available.height() + 120)
    window.show()
    frame = window.frameGeometry()
    assert frame.width() <= available.width()
    assert frame.height() <= available.height()


def test_left_navigation_order_and_security_inside_last_settings(qtbot, session):
    window = MainWindow()
    qtbot.addWidget(window)
    window.activate_session(session)
    window.show()
    assert [window.navigation.item(i).text() for i in range(window.navigation.count())] == ORDER
    assert (
        window.sidebar.geometry().right()
        < window.pages.mapTo(window, window.pages.rect().topLeft()).x()
    )
    assert window.navigation.currentRow() == 0
    window.navigation.setCurrentRow(8)
    assert window.settings_tabs.isVisible() and window.close_book_button.isVisible()
    assert [window.settings_tabs.tabText(i) for i in range(window.settings_tabs.count())] == [
        "Распознавание и переводы",
        "Защита и копии",
        "Оформление",
    ]
    window.settings_tabs.setCurrentIndex(1)
    assert window.backup_status.isVisible()
    assert window.sidebar.isAncestorOf(window.book_title)
    assert window.book_title.text() == session.name


def test_reports_and_bonuses_buttons_dispatch_existing_actions(qtbot, session, monkeypatch):
    seen = []
    monkeypatch.setattr(MainWindow, "request_analytics", lambda self, *, panel: seen.append(panel))
    monkeypatch.setattr(MainWindow, "request_bonuses", lambda self, *, panel: seen.append(panel))
    window = MainWindow()
    qtbot.addWidget(window)
    window.activate_session(session)
    window.show()
    window.navigation.setCurrentRow(2)
    qtbot.mouseClick(window.report_buttons[0], Qt.MouseButton.LeftButton)
    window.navigation.setCurrentRow(1)
    qtbot.mouseClick(window.bonuses_button, Qt.MouseButton.LeftButton)
    assert seen == [window._report_forms, window._bonus_forms]


def test_close_from_settings_clears_workspace_and_reopen_starts_operations(qtbot, session):
    from personal_finance.application.vault import open_vault
    from tests.conftest import PASSWORD

    window = MainWindow()
    qtbot.addWidget(window)
    window.activate_session(session)
    window.show()
    window.navigation.setCurrentRow(8)
    qtbot.mouseClick(window.close_book_button, Qt.MouseButton.LeftButton)
    assert window.session is None and window.root_stack.currentIndex() == 0
    assert window.menuBar().isHidden() and not window.navigation.isVisible()
    assert not window.book_title.text()
    reopened = open_vault(session.root, PASSWORD)
    try:
        window.activate_session(reopened)
        assert window.navigation.currentRow() == 0
        assert window.pages.isAncestorOf(window.ocr_button) and window.ocr_button.isVisible()
        assert window.menuBar().isHidden()
    finally:
        window.lock_book()


def test_catalog_add_actions_open_in_their_pages_and_save(qtbot, session):
    window = MainWindow()
    qtbot.addWidget(window)
    window.activate_session(session)
    window.show()

    def open_form(page, panel, button_text):
        button = next(b for b in page.findChildren(QPushButton) if b.text() == button_text)
        button.click()
        form = panel.stack.currentWidget().widget()
        assert isinstance(form, QDialog) and not form.isWindow()
        return form

    window.navigation.setCurrentRow(3)
    bank = open_form(window.pages.currentWidget(), window._account_forms, "Добавить банк")
    bank.setTextValue("Проверочный банк")
    bank.accept()
    qtbot.waitUntil(lambda: not window._busy, timeout=10000)
    assert [v.name for v in session.catalog.institutions()] == ["Проверочный банк"]

    account = open_form(window.pages.currentWidget(), window._account_forms, "Добавить счёт")
    account.findChild(QLineEdit).setText("Проверочный счёт")
    account.findChild(QDialogButtonBox).button(QDialogButtonBox.StandardButton.Save).click()
    qtbot.waitUntil(lambda: not window._busy, timeout=10000)
    assert [v.name for v in session.catalog.accounts()] == ["Проверочный счёт"]

    window.navigation.setCurrentRow(5)
    category = open_form(window.pages.currentWidget(), window._category_forms, "Добавить категорию")
    category.findChild(QLineEdit).setText("Синтетическая категория")
    category.findChild(QDialogButtonBox).button(QDialogButtonBox.StandardButton.Save).click()
    qtbot.waitUntil(lambda: not window._busy, timeout=10000)
    assert any(v.name == "Синтетическая категория" for v in session.catalog.categories())
    window.lock_book()


def test_backup_and_integrity_run_from_settings_without_action_windows(
    qtbot, session, tmp_path, monkeypatch
):
    backup = tmp_path / "synthetic-copy.pfbackup"
    password = "Синтетический пароль копии"
    monkeypatch.setattr(QFileDialog, "getSaveFileName", lambda *a, **kw: (str(backup), ""))
    monkeypatch.setattr(QFileDialog, "getOpenFileName", lambda *a, **kw: (str(backup), ""))
    window = MainWindow()
    qtbot.addWidget(window)
    window.activate_session(session)
    window.navigation.setCurrentRow(8)
    window.settings_tabs.setCurrentIndex(1)
    window.show()
    panel = window._settings_forms
    page = window.settings_tabs.currentWidget()

    def click(text):
        next(b for b in page.findChildren(QPushButton) if b.text() == text).click()

    def submit(confirm=False):
        dialog = panel.stack.currentWidget().widget()
        assert isinstance(dialog, PasswordDialog) and not dialog.isWindow()
        dialog.password.setText(password)
        if confirm:
            dialog.confirm.setText(password)
        dialog.findChild(QDialogButtonBox).button(QDialogButtonBox.StandardButton.Ok).click()
        qtbot.waitUntil(lambda: not window._busy, timeout=15000)

    click("Создать зашифрованную копию")
    submit(confirm=True)
    assert backup.is_file() and "Создана и проверена" in panel.page_notice.text()
    click("Проверить резервную копию")
    submit()
    contents = panel.stack.currentWidget().widget()
    assert isinstance(contents, BackupContentsDialog) and not contents.isWindow()
    assert contents.windowTitle() == "Состав резервной копии"
    panel.back.click()
    assert page.isVisible()
    click("Проверить целостность книги")
    qtbot.waitUntil(lambda: not window._busy, timeout=15000)
    assert "Целостность базы" in panel.page_notice.text()
    window.lock_book()
    assert not panel.page_notice.text()


def test_journal_keyboard_focus_returns_from_each_inline_form(qtbot, session):
    session.catalog.add_account("Тестовый счёт", AccountKind.CASH)
    session.organization.add_tag("Проверочный тег")
    window = MainWindow()
    qtbot.addWidget(window)
    window.activate_session(session)
    window.show()
    window.activateWindow()
    panel = window._journal_forms
    add = next(b for b in panel.page.findChildren(QPushButton) if b.text() == "Добавить операцию")
    add.setFocus()
    qtbot.waitUntil(lambda: QApplication.focusWidget() is add)
    qtbot.keyClick(add, Qt.Key.Key_Space)

    def focused_form():
        dialog = panel.stack.currentWidget().widget()
        focus = QApplication.focusWidget()
        assert focus is not None and (focus is dialog or dialog.isAncestorOf(focus))
        return dialog, focus

    operation, _ = focused_form()
    assert isinstance(operation, OperationDialog) and not operation.isWindow()
    tag_button = operation.tag_selector.choose
    tag_button.setFocus()
    qtbot.keyClick(tag_button, Qt.Key.Key_Space)
    nested, focus = focused_form()
    assert nested is not operation and panel.stack.count() == 2

    qtbot.keyClick(focus, Qt.Key.Key_Escape)
    assert focused_form() == (operation, tag_button)
    qtbot.keyClick(tag_button, Qt.Key.Key_Escape)
    assert panel.stack.count() == 0 and QApplication.focusWidget() is add
    assert not session.ledger.operations()
    window.lock_book()
