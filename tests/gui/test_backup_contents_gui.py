import pytest
from PySide6.QtWidgets import QDialogButtonBox, QFileDialog, QLabel, QPushButton

from personal_finance.domain.models import AccountKind
from personal_finance.infrastructure.backup.container import create_backup
from personal_finance.presentation.backup_contents import BackupContentsDialog
from personal_finance.presentation.dialogs import PasswordDialog
from personal_finance.presentation.inline_forms import FormHost
from personal_finance.presentation.main_window import MainWindow
from tests.conftest import BACKUP_PASSWORD


def current(panel):
    host = panel.stack.currentWidget()
    assert isinstance(host, FormHost)
    return host.widget()


@pytest.mark.parametrize("book_open", [False, True])
def test_check_copy_shows_contents_without_changing_current_book(
    session, tmp_path, qtbot, monkeypatch, book_open
):
    session.catalog.add_account("Вымышленное имя из архива", AccountKind.CASH)
    archive = create_backup(session, tmp_path / "check.pfbackup", BACKUP_PASSWORD)
    monkeypatch.setattr(QFileDialog, "getOpenFileName", lambda *a, **k: (str(archive), ""))
    window = MainWindow()
    qtbot.addWidget(window)
    window.show()
    if book_open:
        window.activate_session(session)
        window.navigation.setCurrentRow(8)
        window.settings_tabs.setCurrentIndex(1)
        panel = window._settings_forms
    else:
        panel = window._welcome_forms
    try:
        action = next(
            button
            for button in window.findChildren(QPushButton)
            if button.isVisible() and button.text() == "Проверить резервную копию"
        )
        action.click()
        dialog = current(panel)
        assert isinstance(dialog, PasswordDialog)
        dialog.password.setText(BACKUP_PASSWORD)
        dialog.findChild(QDialogButtonBox).button(QDialogButtonBox.StandardButton.Ok).click()
        qtbot.waitUntil(lambda: not window._busy, timeout=15000)
        result = current(panel)
        assert isinstance(result, BackupContentsDialog)
        values = [label.text() for label in result.findChildren(QLabel)]
        assert "Схема в копии" in values and "0017" in values and "1" in values
        assert all(BACKUP_PASSWORD not in value and "Вымышленное" not in value for value in values)
        assert window.session is (session if book_open else None)
    finally:
        window.lock_book()
