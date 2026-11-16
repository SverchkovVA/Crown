from contextlib import closing

import pytest
from PySide6.QtWidgets import QDialogButtonBox, QLineEdit, QPushButton

from personal_finance.application.vault import open_vault
from personal_finance.domain.errors import InvalidCredentials
from personal_finance.domain.models import AccountKind
from personal_finance.presentation.dialogs import ChangePasswordDialog
from personal_finance.presentation.inline_forms import FormHost
from personal_finance.presentation.main_window import MainWindow
from tests.conftest import PASSWORD

REPLACEMENT = "  новая вымышленная фраза  "


def test_change_password_from_security_settings(session, qtbot):
    root = session.root
    manifest = root / "data/key.json"
    original = manifest.read_bytes()
    account = session.catalog.add_account("Учебный счёт", AccountKind.CASH)
    window = MainWindow()
    qtbot.addWidget(window)
    window.show()
    window.activate_session(session)
    window.navigation.setCurrentRow(8)
    window.settings_tabs.setCurrentIndex(1)
    panel = window._settings_forms
    action = next(
        button
        for button in window.findChildren(QPushButton)
        if button.isVisible() and button.text() == "Сменить парольную фразу…"
    )
    try:
        action.click()
        host = panel.stack.currentWidget()
        assert isinstance(host, FormHost)
        dialog = host.widget()
        assert isinstance(dialog, ChangePasswordDialog) and not dialog.isWindow()
        fields = (dialog.current, dialog.replacement, dialog.confirm)
        assert all(field.echoMode() == QLineEdit.EchoMode.Password for field in fields)
        save = dialog.findChild(QDialogButtonBox).button(QDialogButtonBox.StandardButton.Save)

        dialog.current.setText(PASSWORD)
        dialog.replacement.setText(REPLACEMENT)
        dialog.confirm.setText("не совпадает")
        save.click()
        assert "не совпадают" in panel.notice.text()
        assert not window._busy and manifest.read_bytes() == original

        panel.back.click()
        assert all(not field.text() and not field.isUndoAvailable() for field in fields)
        panel.forward.click()
        assert panel.stack.currentWidget() is host

        dialog.current.setText("неверная текущая фраза")
        dialog.replacement.setText(REPLACEMENT)
        dialog.confirm.setText(REPLACEMENT)
        save.click()
        qtbot.waitUntil(lambda: not window._busy, timeout=20000)
        assert panel.stack.currentWidget() is host and panel.notice.text()
        assert all(not field.text() and not field.isUndoAvailable() for field in fields)
        assert manifest.read_bytes() == original

        dialog.current.setText(PASSWORD)
        dialog.replacement.setText(REPLACEMENT)
        dialog.confirm.setText(REPLACEMENT)
        save.click()
        qtbot.waitUntil(lambda: not window._busy, timeout=20000)
        assert panel.page.isVisible() and window.session is session
        assert panel.page_notice is not None
        assert panel.page_notice.text() == "Парольная фраза заменена."
    finally:
        window.lock_book()

    with pytest.raises(InvalidCredentials):
        open_vault(root, PASSWORD)
    with closing(open_vault(root, REPLACEMENT)) as reopened:
        assert reopened.catalog.accounts()[0].id == account.id
