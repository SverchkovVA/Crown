import hashlib

import pytest
from sqlalchemy import text

from personal_finance.application import vault as vault_module
from personal_finance.domain.models import AccountKind
from personal_finance.infrastructure.backup.container import (
    create_backup,
    restore_backup,
    verify_backup,
)
from personal_finance.infrastructure.persistence import migration_runner
from tests.conftest import BACKUP_PASSWORD, PASSWORD


@pytest.fixture
def legacy_session(tmp_path, monkeypatch):
    with monkeypatch.context() as patch:
        patch.setattr(
            vault_module,
            "initialize_schema",
            lambda engine: migration_runner.initialize_schema(engine, target="0002"),
        )
        session, _ = vault_module.create_vault(
            tmp_path / "legacy.pfvault", "Старая схема", PASSWORD
        )
    session.catalog.add_account("Проверочный счёт старой схемы", AccountKind.CASH)
    try:
        yield session
    finally:
        session.close()


def _hashes(session):
    return {
        name: hashlib.sha256((session.root / "data" / name).read_bytes()).hexdigest()
        for name in ("key.json", "ledger.sqlite3")
    }


def test_old_book_opens_unchanged_and_restore_upgrades_only_new_folder(legacy_session, tmp_path):
    source = legacy_session
    before = _hashes(source)
    assert source.schema_revision == "0002"
    backup = create_backup(source, tmp_path / "legacy.pfbackup", BACKUP_PASSWORD)
    assert verify_backup(backup, BACKUP_PASSWORD)["schema"] == "0002"
    restored, recovery = restore_backup(
        backup,
        tmp_path / "upgraded.pfvault",
        BACKUP_PASSWORD,
        "новый пароль новой схемы",
    )
    try:
        assert restored.schema_revision == "0013"
        assert restored.book_id == source.book_id
        assert restored.replica_id != source.replica_id
        assert restored.catalog.accounts() == source.catalog.accounts()
        with restored.engine.connect() as connection:
            assert connection.execute(text("SELECT count(*) FROM postings")).scalar_one() == 0
        restored.check_integrity()
    finally:
        restored.close()
    assert recovery.startswith("PF1-")
    assert _hashes(source) == before
    source.check_integrity()
    source.close()
    reopened = vault_module.open_vault(source.root, PASSWORD)
    try:
        assert reopened.schema_revision == "0002"
        assert _hashes(reopened) == before
    finally:
        reopened.close()


def test_failed_migration_never_publishes_or_changes_old_book(
    legacy_session, tmp_path, monkeypatch
):
    source = legacy_session
    before = _hashes(source)
    backup = create_backup(source, tmp_path / "legacy.pfbackup", BACKUP_PASSWORD)
    original_upgrade = migration_runner.command.upgrade

    def fail_after_ddl(*args, **kwargs):
        original_upgrade(*args, **kwargs)
        raise OSError("Синтетический отказ после DDL")

    monkeypatch.setattr(migration_runner.command, "upgrade", fail_after_ddl)
    target = tmp_path / "failed.pfvault"
    with pytest.raises(OSError):
        restore_backup(backup, target, BACKUP_PASSWORD, "новый пароль новой схемы")
    assert not target.exists()
    assert _hashes(source) == before
    assert verify_backup(backup, BACKUP_PASSWORD)["schema"] == "0002"
    source.check_integrity()
