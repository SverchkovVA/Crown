from pathlib import Path

import pytest

from personal_finance.application.vault import open_vault
from personal_finance.domain.errors import FinanceError
from personal_finance.domain.models import AccountKind
from personal_finance.infrastructure.backup.container import (
    create_backup,
    restore_backup,
    verify_backup,
)
from tests.conftest import BACKUP_PASSWORD


def test_restore_with_new_password_and_replica(session, tmp_path: Path) -> None:
    original_account = session.catalog.add_account("Резервные наличные", AccountKind.CASH)
    backup = create_backup(session, tmp_path / "full.pfbackup", BACKUP_PASSWORD)
    assert "Резервные наличные".encode() not in backup.read_bytes()
    assert session.backup_key() not in backup.read_bytes()
    restored, recovery = restore_backup(
        backup, tmp_path / "restored.pfvault", BACKUP_PASSWORD, "новый пароль восстановленной книги"
    )
    try:
        assert restored.book_id == session.book_id
        assert restored.replica_id != session.replica_id
        assert restored.catalog.accounts()[0].id == original_account.id
        restored.check_integrity()
    finally:
        restored.close()
    reopened = open_vault(tmp_path / "restored.pfvault", recovery, recovery=True)
    reopened.close()


@pytest.mark.parametrize("damage", ["truncate", "flip", "append", "wrong_password"])
def test_invalid_backup_never_publishes_book(session, tmp_path: Path, damage: str) -> None:
    backup = create_backup(session, tmp_path / "copy.pfbackup", BACKUP_PASSWORD)
    raw = bytearray(backup.read_bytes())
    if damage == "truncate":
        raw = raw[:-10]
    elif damage == "flip":
        raw[len(raw) // 2] ^= 1
    elif damage == "append":
        raw += b"unexpected"
    backup.write_bytes(raw)
    target = tmp_path / "restored.pfvault"
    password = "неверный пароль резервирования" if damage == "wrong_password" else BACKUP_PASSWORD
    with pytest.raises(FinanceError):
        restore_backup(backup, target, password, "новая безопасная парольная фраза")
    assert not target.exists()
    session.check_integrity()


def test_backup_never_overwrites_existing_file(session, tmp_path: Path) -> None:
    target = tmp_path / "existing.pfbackup"
    target.write_bytes(b"important")
    with pytest.raises(FinanceError):
        create_backup(session, target, BACKUP_PASSWORD)
    assert target.read_bytes() == b"important"


def test_verification_does_not_restore_files(session, tmp_path: Path) -> None:
    backup = create_backup(session, tmp_path / "copy.pfbackup", BACKUP_PASSWORD)
    before = set(tmp_path.iterdir())
    result = verify_backup(backup, BACKUP_PASSWORD)
    assert result["book_id"] == session.book_id
    assert "database_key" not in result
    assert set(tmp_path.iterdir()) == before
