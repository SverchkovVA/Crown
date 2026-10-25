import socket

from personal_finance.application.vault import create_vault
from personal_finance.domain.models import AccountKind
from personal_finance.infrastructure.backup.container import create_backup, restore_backup


def test_book_catalog_and_backup_do_not_use_network(tmp_path, monkeypatch) -> None:
    def blocked(*args, **kwargs):
        raise AssertionError("Сетевая операция запрещена в автономном сценарии")

    monkeypatch.setattr(socket, "socket", blocked)
    monkeypatch.setattr(socket, "create_connection", blocked)
    source, _ = create_vault(tmp_path / "offline.pfvault", "Офлайн", "пароль автономной книги")
    try:
        source.catalog.add_account("Наличные", AccountKind.CASH)
        backup = create_backup(source, tmp_path / "offline.pfbackup", "пароль автономной копии")
        restored, _ = restore_backup(
            backup,
            tmp_path / "restored.pfvault",
            "пароль автономной копии",
            "новый автономный пароль",
        )
        restored.close()
    finally:
        source.close()
