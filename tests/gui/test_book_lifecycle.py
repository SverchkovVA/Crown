import hashlib

import pytest
from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
)

from personal_finance.application.vault import create_vault, open_vault
from personal_finance.domain.models import AccountKind
from personal_finance.infrastructure.backup.container import create_backup, restore_backup
from personal_finance.presentation.dialogs import CreateDialog, OpenDialog, RecoveryDialog
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
    assert window.account_model.item(0, 0).text() == "Синтетический счёт"
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


@pytest.mark.parametrize("input_method", ["keyboard", "paste_shortcut", "padded_paste"])
def test_recovery_code_input_events_select_mode_and_open_book(qtbot, tmp_path, input_method):
    app = QApplication.instance()
    assert app is not None
    if input_method != "keyboard" and app.platformName() != "offscreen":
        pytest.skip("Буфер обмена проверяется только изолированной offscreen-платформой Qt")
    path = tmp_path / "input-events.pfvault"
    session, recovery = create_vault(path, "Синтетическая проверка ввода", PASSWORD)
    session.close()
    dialog = OpenDialog(path)
    qtbot.addWidget(dialog)
    dialog.show()
    dialog.credential.setFocus()
    assert not dialog.recovery.isChecked()

    if input_method == "keyboard":
        qtbot.keyClicks(dialog.credential, recovery)
    else:
        clipboard = app.clipboard()
        # У offscreen собственный буфер в памяти: системный буфер с кодом
        # пользователя не читается и не заменяется синтетическими данными.
        value = f"  {recovery}  " if input_method == "padded_paste" else recovery
        clipboard.setText(value)
        try:
            qtbot.keyClick(dialog.credential, Qt.Key.Key_V, Qt.KeyboardModifier.ControlModifier)
        finally:
            clipboard.clear()

    assert dialog.credential.text().strip() == recovery
    assert dialog.recovery.isChecked()
    reopened = open_vault(
        path, dialog.credential.text().strip(), recovery=dialog.recovery.isChecked()
    )
    reopened.close()

    # После ручного выбора режима он остаётся видимым при потере фокуса
    # и подтверждении окна; смена placeholder не должна сбрасывать выбор.
    qtbot.mouseClick(dialog.recovery, Qt.MouseButton.LeftButton)
    assert not dialog.recovery.isChecked()
    dialog.accept()
    assert not dialog.recovery.isChecked()


@pytest.mark.parametrize("restore", [False, True])
def test_inline_book_entry_keeps_recovery_step_and_reopens_from_recent(
    qtbot, tmp_path, monkeypatch, restore
):
    target = tmp_path / "Новая проверочная книга.pfvault"
    if restore:
        source, _ = _create_book(tmp_path / "Исходная книга.pfvault")
        backup = create_backup(source, tmp_path / "Синтетическая копия.pfbackup", BACKUP_PASSWORD)
        source.close()
        monkeypatch.setattr(QFileDialog, "getOpenFileName", lambda *args: (str(backup), ""))

    window = MainWindow()
    qtbot.addWidget(window)
    window.show()
    panel = window._welcome_forms
    caption = "Восстановить из резервной копии" if restore else "Создать книгу"
    next(b for b in panel.page.findChildren(QPushButton) if b.text() == caption).click()
    entry = panel.stack.currentWidget().widget()
    assert isinstance(entry, CreateDialog) and not entry.isWindow()
    entry.path.setText(str(target))
    entry.name.setText("Новая проверочная книга")
    entry.password.setText(PASSWORD)
    entry.confirm.setText(PASSWORD)
    if restore:
        entry.backup_password.setText(BACKUP_PASSWORD)
    entry.findChild(QDialogButtonBox).button(QDialogButtonBox.StandardButton.Ok).click()
    qtbot.waitUntil(lambda: not window._busy, timeout=20000)
    assert window.session is not None and window.session.root == target
    if restore:
        assert window.session.catalog.accounts()[0].name == "Синтетический счёт"

    recovery = panel.stack.currentWidget().widget()
    assert isinstance(recovery, RecoveryDialog) and not recovery.isWindow()
    assert not window.navigation.isVisible()
    done = recovery.findChild(QDialogButtonBox).button(QDialogButtonBox.StandardButton.Ok)
    assert not done.isEnabled()
    qtbot.keyClick(recovery, Qt.Key.Key_Escape)
    assert panel.stack.currentWidget().widget() is recovery and not window.navigation.isVisible()
    code = recovery.findChild(QPlainTextEdit).toPlainText()
    recovery.findChild(QCheckBox).setChecked(True)
    done.click()
    assert window.navigation.isVisible() and panel.stack.count() == 0

    window.lock_book()
    window.recent_books_list.setCurrentRow(0)
    window.open_recent_button.click()
    dialog = panel.stack.currentWidget().widget()
    assert isinstance(dialog, OpenDialog) and not dialog.isWindow()
    dialog.credential.setText(code)
    assert dialog.recovery.isChecked()
    dialog.findChild(QDialogButtonBox).button(QDialogButtonBox.StandardButton.Open).click()
    qtbot.waitUntil(lambda: not window._busy, timeout=15000)
    assert window.session is not None and window.session.root == target
    assert window.navigation.isVisible()
    window.lock_book()
