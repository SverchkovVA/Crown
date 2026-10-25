import hashlib

import pytest
from PySide6.QtWidgets import QDialog, QFileDialog, QMessageBox

from personal_finance.application.vault import create_vault, open_vault
from personal_finance.domain.models import AccountKind
from personal_finance.infrastructure.backup.container import create_backup, restore_backup
from personal_finance.presentation.dialogs import OpenDialog
from personal_finance.presentation.main_window import MainWindow
from tests.conftest import BACKUP_PASSWORD, PASSWORD


def _create_book(path):
    session, recovery = create_vault(path, "Проверка закрытия", PASSWORD)
    session.catalog.add_account("Синтетический счёт", AccountKind.CASH)
    return session, recovery


@pytest.mark.parametrize("origin", ["create", "open", "restore"])
@pytest.mark.parametrize("close_window", [False, True])
def test_worker_session_reopens_after_gui_close(qtbot, tmp_path, monkeypatch, origin, close_window):
    path = tmp_path / "Книга с пробелами.pfvault"
    if origin != "create":
        source, recovery = _create_book(tmp_path / "source.pfvault")
        source_path = source.root
        if origin == "restore":
            backup = create_backup(source, tmp_path / "source.pfbackup", BACKUP_PASSWORD)
        else:
            path = source_path
        source.close()

    def action():
        if origin == "create":
            return _create_book(path)
        if origin == "restore":
            return restore_backup(backup, path, BACKUP_PASSWORD, PASSWORD)
        return open_vault(path, PASSWORD), recovery

    errors = []
    monkeypatch.setattr(QMessageBox, "warning", lambda *args: errors.append(args[-1]))
    window = MainWindow()
    qtbot.addWidget(window)
    window.show()
    results = []

    def activated(result):
        results.append(result)
        window.activate_session(result[0])

    window._job(action, activated, "Проверка открытия…")
    qtbot.waitUntil(lambda: not window._busy, timeout=15000)
    assert not errors
    assert results
    recovery = results[0][1]
    manifest = path / "data" / "key.json"
    original_hash = hashlib.sha256(manifest.read_bytes()).digest()
    assert window.account_table.item(0, 0).text() == "Синтетический счёт"
    if close_window:
        window.close()
    else:
        window.lock_book()
    assert window.session is None

    for credential, use_recovery in [(PASSWORD, False), (recovery, True)]:
        reopened = open_vault(path, credential, recovery=use_recovery)
        try:
            assert reopened.catalog.accounts()[0].name == "Синтетический счёт"
            reopened.check_integrity()
        finally:
            reopened.close()
    assert hashlib.sha256(manifest.read_bytes()).digest() == original_hash

    # На Windows переименование каталога дополнительно выявляет оставленные handles.
    moved = path.with_name("Закрытая книга.pfvault")
    path.rename(moved)
    moved.rename(path)


@pytest.mark.parametrize(
    "method",
    ["password", "recovery", "padded_recovery", "recovery_then_password", "code_like_password"],
)
def test_open_dialog_selects_recovery_and_preserves_password(qtbot, tmp_path, monkeypatch, method):
    path = tmp_path / "book.pfvault"
    password_modes = {"password", "recovery_then_password", "code_like_password"}
    password = "PF1-" + "A" * 52 if method == "code_like_password" else f"  {PASSWORD}  "
    session, recovery = create_vault(path, "Проверка режима", password)
    session.close()
    credential = password if method in password_modes else recovery
    if method == "padded_recovery":
        credential = f"  {recovery}  "
    selected_modes = []

    def accept_open(dialog):
        if method == "recovery_then_password":
            dialog.credential.setText(recovery)
        dialog.credential.setText(credential)
        if method == "code_like_password":
            # Пользователь может явно выбрать пароль, даже если он похож на код.
            dialog.recovery.setChecked(False)
        selected_modes.append(dialog.recovery.isChecked())
        return QDialog.DialogCode.Accepted

    monkeypatch.setattr(QFileDialog, "getExistingDirectory", lambda *args: str(path))
    monkeypatch.setattr(OpenDialog, "exec", accept_open)
    errors = []
    monkeypatch.setattr(QMessageBox, "warning", lambda *args: errors.append(args[-1]))
    window = MainWindow()
    qtbot.addWidget(window)
    window.open_book()
    qtbot.waitUntil(lambda: not window._busy, timeout=15000)
    assert selected_modes == [method not in password_modes]
    assert not errors
    assert window.session is not None
    window.lock_book()
    reopened = open_vault(path, password)
    reopened.close()
