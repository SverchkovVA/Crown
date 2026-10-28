"""Миграция OCR выполняется только над синтетической восстановленной копией."""

import hashlib
from datetime import date

import pytest
from alembic import op
from sqlalchemy import text
from sqlalchemy.exc import DatabaseError

from personal_finance.application import vault as vault_module
from personal_finance.domain.errors import VaultError
from personal_finance.domain.ledger import OperationDraft, OperationKind
from personal_finance.domain.models import AccountKind, CategoryKind, Money
from personal_finance.infrastructure.backup.container import (
    create_backup,
    restore_backup,
    verify_backup,
)
from personal_finance.infrastructure.persistence.migration_runner import (
    initialize_schema,
    upgrade_restored_schema,
)
from tests.conftest import BACKUP_PASSWORD, PASSWORD


@pytest.fixture
def old9(tmp_path, monkeypatch):
    with monkeypatch.context() as patch:
        patch.setattr(
            vault_module,
            "initialize_schema",
            lambda engine: initialize_schema(engine, target="0009"),
        )
        opened, _ = vault_module.create_vault(
            tmp_path / "original9.pfvault", "Синтетическая книга 0009", PASSWORD
        )
    try:
        yield opened
    finally:
        opened.close()


def guards(connection):
    return dict(
        connection.exec_driver_sql(
            "SELECT name,sql FROM sqlite_master WHERE type='trigger'"
        ).fetchall()
    )


def add_original_operation(session):
    account = session.catalog.add_account("Счёт перед OCR-миграцией", AccountKind.CASH)
    category = next(c.id for c in session.catalog.categories() if c.kind == CategoryKind.EXPENSE)
    return session.ledger.add(
        OperationDraft(OperationKind.EXPENSE, date(2026, 9, 21), account.id, Money(81123), category)
    )


def source_hashes(root):
    return tuple(
        hashlib.sha256((root / "data" / name).read_bytes()).hexdigest()
        for name in ("ledger.sqlite3", "key.json")
    )


def test_migration10_preserves_every_existing_trigger_and_financial_revision(old9):
    operation = add_original_operation(old9)
    tag = old9.organization.add_tag("Метка до OCR")
    before = (old9.ledger.operations(), old9.ledger.balances(), old9.ledger.history(operation.id))
    with old9.engine.connect() as connection:
        previous = guards(connection)
    upgrade_restored_schema(old9.engine, "0009")
    with old9.engine.connect() as connection:
        current = guards(connection)
        assert all(current[name] == sql for name, sql in previous.items())
        assert (
            connection.execute(text("SELECT version_num FROM alembic_version")).scalar_one()
            == "0010"
        )
        assert connection.execute(text("SELECT count(*) FROM ocr_records")).scalar_one() == 0
        assert (
            connection.execute(
                text("SELECT strict FROM pragma_table_list WHERE name='ocr_records'")
            ).scalar_one()
            == 1
        )
    assert (
        old9.ledger.operations(),
        old9.ledger.balances(),
        old9.ledger.history(operation.id),
    ) == before
    with pytest.raises(DatabaseError), old9.engine.begin() as connection:
        connection.execute(
            text("UPDATE tag_revisions SET name='Не менять' WHERE revision_id=:id"),
            {"id": tag.revision_id},
        )
    root = old9.root
    old9.close()
    reopened = vault_module.open_vault(root, PASSWORD)
    try:
        assert reopened.schema_revision == "0010" and reopened.ocr_importing is not None
        reopened.check_integrity()
    finally:
        reopened.close()


def test_fault_after_new_index_rolls_back_ocr_schema_and_keeps_old_guards(old9, monkeypatch):
    operation = add_original_operation(old9)
    with old9.engine.connect() as connection:
        previous = guards(connection)
    create_index = op.create_index
    called = []

    def fail(name, *args, **kwargs):
        result = create_index(name, *args, **kwargs)
        if name == "ix_ocr_image":
            called.append(True)
            raise OSError("Синтетический отказ после OCR-индекса")
        return result

    monkeypatch.setattr(op, "create_index", fail)
    with pytest.raises(OSError, match="Синтетический отказ"):
        upgrade_restored_schema(old9.engine, "0009")
    assert called == [True]
    with old9.engine.connect() as connection:
        assert (
            connection.execute(text("SELECT version_num FROM alembic_version")).scalar_one()
            == "0009"
        )
        assert guards(connection) == previous
        assert (
            connection.execute(
                text("SELECT name FROM sqlite_master WHERE name IN ('ocr_records','ix_ocr_image')")
            ).fetchall()
            == []
        )
        assert connection.execute(text("PRAGMA foreign_keys")).scalar_one() == 1
    assert old9.ledger.operations() == [operation]
    old9.check_integrity()


def test_opening_schema9_never_migrates_it_and_restore_publishes_only_new_schema10(old9, tmp_path):
    operation = add_original_operation(old9)
    root = old9.root
    old9.close()
    before = source_hashes(root)
    opened = vault_module.open_vault(root, PASSWORD)
    restored = None
    try:
        assert opened.schema_revision == "0009" and opened.ocr_importing is None
        backup = create_backup(opened, tmp_path / "old9.pfbackup", BACKUP_PASSWORD)
        assert verify_backup(backup, BACKUP_PASSWORD)["schema"] == "0009"
        restored, _ = restore_backup(backup, tmp_path / "new10.pfvault", BACKUP_PASSWORD, PASSWORD)
        assert restored.schema_revision == "0010" and restored.ocr_importing is not None
        assert restored.book_id == opened.book_id and restored.replica_id != opened.replica_id
        assert restored.ledger.operations() == [operation]
        restored.check_integrity()
    finally:
        opened.close()
        if restored:
            restored.close()
    assert source_hashes(root) == before


def test_failed_restore9to10_never_publishes_or_modifies_source_or_backup(
    old9, tmp_path, monkeypatch
):
    add_original_operation(old9)
    backup = create_backup(old9, tmp_path / "before-fault.pfbackup", BACKUP_PASSWORD)
    root = old9.root
    old9.close()
    before = source_hashes(root)
    backup_hash = hashlib.sha256(backup.read_bytes()).hexdigest()
    create_table = op.create_table
    called = []

    def fail(name, *args, **kwargs):
        result = create_table(name, *args, **kwargs)
        if name == "ocr_records":
            called.append(True)
            raise OSError("Синтетический отказ OCR-восстановления")
        return result

    monkeypatch.setattr(op, "create_table", fail)
    target = tmp_path / "must-not-exist.pfvault"
    with pytest.raises((OSError, VaultError)):
        restore_backup(backup, target, BACKUP_PASSWORD, PASSWORD)
    assert called == [True] and not target.exists()
    assert source_hashes(root) == before
    assert hashlib.sha256(backup.read_bytes()).hexdigest() == backup_hash
