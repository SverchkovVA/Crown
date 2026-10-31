"""Пользовательская навигация и границы экрана закрытой книги."""

from PySide6.QtCore import Qt

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


def test_right_navigation_order_and_security_inside_last_settings(qtbot, session):
    window = MainWindow()
    qtbot.addWidget(window)
    window.activate_session(session)
    window.show()
    assert [window.navigation.item(i).text() for i in range(window.navigation.count())] == ORDER
    assert (
        window.sidebar.geometry().left()
        > window.pages.mapTo(window, window.pages.rect().topLeft()).x()
    )
    assert window.navigation.currentRow() == 0
    window.navigation.setCurrentRow(8)
    assert window.settings_tabs.isVisible() and window.close_book_button.isVisible()
    assert [window.settings_tabs.tabText(i) for i in range(window.settings_tabs.count())] == [
        "Распознавание и переводы",
        "Защита и копии",
    ]
    window.settings_tabs.setCurrentIndex(1)
    assert window.backup_status.isVisible()
    assert window.sidebar.isAncestorOf(window.book_title)
    assert window.book_title.text() == session.name


def test_reports_and_bonuses_buttons_dispatch_existing_actions(qtbot, session, monkeypatch):
    seen = []
    monkeypatch.setattr(MainWindow, "request_analytics", lambda self: seen.append("report"))
    monkeypatch.setattr(MainWindow, "request_bonuses", lambda self: seen.append("bonuses"))
    window = MainWindow()
    qtbot.addWidget(window)
    window.activate_session(session)
    window.show()
    window.navigation.setCurrentRow(2)
    qtbot.mouseClick(window.report_buttons[0], Qt.MouseButton.LeftButton)
    window.navigation.setCurrentRow(1)
    qtbot.mouseClick(window.bonuses_button, Qt.MouseButton.LeftButton)
    assert seen == ["report", "bonuses"]


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
