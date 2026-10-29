import hashlib

import pytest
from alembic import op
from sqlalchemy import text

from personal_finance.application import vault
from personal_finance.infrastructure.backup.container import create_backup, restore_backup
from personal_finance.infrastructure.persistence.migration_runner import (
    initialize_schema,
    upgrade_restored_schema,
)
from tests.conftest import BACKUP_PASSWORD, PASSWORD


@pytest.fixture
def old10(tmp_path, monkeypatch):
    with monkeypatch.context() as patch:
        patch.setattr(
            vault, "initialize_schema", lambda engine: initialize_schema(engine, target="0010")
        )
        session, _ = vault.create_vault(
            tmp_path / "old.pfvault", "Вымышленная книга 0010", PASSWORD
        )
    session.catalog.add_institution("Вымышленный банк пользователя")
    try:
        yield session
    finally:
        session.close()


def guards(connection):
    return dict(
        connection.exec_driver_sql(
            "SELECT name,sql FROM sqlite_master WHERE type='trigger'"
        ).fetchall()
    )


def fingerprint(root):
    return tuple(
        hashlib.sha256((root / "data" / name).read_bytes()).hexdigest()
        for name in ("ledger.sqlite3", "key.json")
    )


def test_open_schema10_keeps_original_and_restore_adds_only_new_workflow(old10, tmp_path):
    source = old10.root
    banks = old10.catalog.institutions()
    old10.close()
    before = fingerprint(source)
    opened = vault.open_vault(source, PASSWORD)
    try:
        assert opened.schema_revision == "0010" and opened.ocr_settings is None
        assert not opened.ocr_importing.supports_batches
        with opened.engine.connect() as connection:
            previous = guards(connection)
        backup = create_backup(opened, tmp_path / "old.pfbackup", BACKUP_PASSWORD)
        restored, _ = restore_backup(backup, tmp_path / "new.pfvault", BACKUP_PASSWORD, PASSWORD)
        try:
            assert restored.schema_revision == "0013" and restored.ocr_settings is not None
            assert (
                restored.ocr_importing.supports_batches and restored.catalog.institutions() == banks
            )
            with restored.engine.connect() as connection:
                current = guards(connection)
            assert all(
                current[name] == value
                for name, value in previous.items()
                if name != "revision_payload_valid"
            )
            assert "deposit_scenario_revisions" in current["revision_payload_valid"]
            suffix = ") BEGIN SELECT RAISE(ABORT, 'missing typed payload'); END"
            assert (
                previous["revision_payload_valid"].removesuffix(suffix)
                in current["revision_payload_valid"]
            )
            restored.check_integrity()
        finally:
            restored.close()
    finally:
        opened.close()
    assert fingerprint(source) == before


def test_migration11_fault_rolls_back_tables_and_preserves_all_guards(old10, monkeypatch):
    with old10.engine.connect() as connection:
        previous = guards(connection)
    original = op.execute

    def fail(statement, *args, **kwargs):
        if str(statement).startswith("CREATE TABLE ocr_batches"):
            raise OSError("Вымышленный отказ после создания настроек")
        return original(statement, *args, **kwargs)

    monkeypatch.setattr(op, "execute", fail)
    with pytest.raises(OSError):
        upgrade_restored_schema(old10.engine, "0010")
    with old10.engine.connect() as connection:
        assert (
            connection.exec_driver_sql("SELECT version_num FROM alembic_version").scalar_one()
            == "0010"
        )
        assert guards(connection) == previous
        assert not connection.execute(
            text("SELECT name FROM sqlite_master WHERE name='ocr_settings_versions'")
        ).first()
    old10.check_integrity()
