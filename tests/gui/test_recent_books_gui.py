from PySide6.QtCore import QSettings
from PySide6.QtWidgets import QDialog, QMessageBox

from personal_finance.infrastructure.recent_books import LocalRecentBooks
from personal_finance.presentation.dialogs import OpenDialog
from personal_finance.presentation.main_window import MainWindow


def recent(tmp_path):
    return LocalRecentBooks(QSettings(str(tmp_path / "books.ini"), QSettings.Format.IniFormat))


def test_successful_open_remember_and_per_book_opt_out_survive_restart(qtbot, session, tmp_path):
    store = recent(tmp_path)
    window = MainWindow(recent_books=store)
    qtbot.addWidget(window)
    key_before = (session.root / "data/key.json").read_bytes()
    window.activate_session(session)
    assert len(store.items()) == 1
    assert window.recent_books_list.count() == 1 and window.remember_book_check.isChecked()
    window.remember_book_check.setChecked(False)
    assert not recent(tmp_path).enabled(session.book_id) and not store.items()
    window.lock_book()
    assert not window.session and (session.root / "data/key.json").read_bytes() == key_before
    assert window.recent_books_list.count() == 0
    from personal_finance.application.vault import open_vault
    from tests.conftest import PASSWORD

    reopened = open_vault(session.root, PASSWORD)
    window.activate_session(reopened)
    assert not window.remember_book_check.isChecked() and not store.items()
    window.lock_book()


def test_selected_recent_path_opens_password_dialog_without_folder_picker(
    qtbot, tmp_path, monkeypatch
):
    from uuid import uuid4

    path = tmp_path / "example.pfvault"
    path.mkdir()
    store = recent(tmp_path)
    store.remember(path, str(uuid4()), "Пример")
    window = MainWindow(recent_books=store)
    qtbot.addWidget(window)
    assert not window.open_recent_button.isEnabled()
    opened, jobs = [], []

    def prompt(dialog):
        opened.append(dialog)
        assert dialog.credential.text() == ""
        dialog.credential.setText("test-input-only")
        return QDialog.DialogCode.Accepted

    monkeypatch.setattr(OpenDialog, "exec", prompt)
    monkeypatch.setattr(window, "_job", lambda *args, **kwargs: jobs.append(args))
    window.recent_books_list.setCurrentRow(0)
    window.open_recent_book()
    assert len(opened) == len(jobs) == 1 and window.session is None
    assert "test-input-only" not in str(store.settings.value("recent_books_v1"))


def test_missing_recent_path_gives_message_and_can_be_forgotten(qtbot, tmp_path, monkeypatch):
    from uuid import uuid4

    store = recent(tmp_path)
    store.remember(tmp_path / "missing.pfvault", str(uuid4()), "Недоступна")
    window = MainWindow(recent_books=store)
    qtbot.addWidget(window)
    messages = []
    monkeypatch.setattr(QMessageBox, "information", lambda *args: messages.append(True))
    window.recent_books_list.setCurrentRow(0)
    window.open_recent_book()
    assert messages and len(store.items()) == 1
    window.forget_recent_book()
    assert not store.items() and window.recent_books_list.count() == 0
