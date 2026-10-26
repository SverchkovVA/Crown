from PySide6.QtCore import QThread
from PySide6.QtWidgets import QMessageBox

from personal_finance.domain.models import AccountKind
from personal_finance.presentation.main_window import MainWindow


def test_real_session_displayed_and_cleared_on_lock(qtbot, session) -> None:
    session.catalog.add_account(
        "Проверочная карта", AccountKind.DEBIT, session.catalog.institutions()[0].id
    )
    window = MainWindow()
    qtbot.addWidget(window)
    window.activate_session(session)
    window.show()
    assert window.book_title.text() == "Тестовая книга"
    assert window.account_model.item(0, 0).text() == "Проверочная карта"
    assert window.category_tree.topLevelItemCount() == 8
    window.lock_book()
    assert window.session is None
    assert window.account_model.rowCount() == 0
    assert window.category_tree.topLevelItemCount() == 0
    assert window.book_title.text() == ""


def test_background_job_returns_to_gui_thread(qtbot, session) -> None:
    window = MainWindow()
    qtbot.addWidget(window)
    window.activate_session(session)
    results = []

    def completed(result) -> None:
        results.append((result, QThread.currentThread() == window.thread()))
        window.refresh()

    window._job(
        lambda: session.catalog.add_account("Фоновый счёт", AccountKind.CASH),
        completed,
        "Проверка…",
    )
    qtbot.waitUntil(lambda: not window._busy, timeout=10000)
    assert results[0][1] is True
    assert window.account_model.item(0, 0).text() == "Фоновый счёт"


def test_invalid_password_job_does_not_activate_book(qtbot, session, monkeypatch) -> None:
    from personal_finance.application.vault import open_vault

    root = session.root
    session.close()
    window = MainWindow()
    qtbot.addWidget(window)
    errors = []
    monkeypatch.setattr(QMessageBox, "warning", lambda *args: errors.append(args[-1]))
    window._job(
        lambda: open_vault(root, "неверная парольная фраза"), window.activate_session, "Открытие…"
    )
    qtbot.waitUntil(lambda: not window._busy, timeout=10000)
    assert window.session is None
    assert errors and "пароль" in errors[0]
