"""Обязательный явный переход 0014 до активации SCHEMA_HEAD; только синтетические книги."""

from datetime import date
from hashlib import sha256

import pytest
from sqlalchemy import event

from personal_finance.application import vault
from personal_finance.domain.bonuses import BonusDraft, BonusKind
from personal_finance.domain.models import AccountKind
from personal_finance.infrastructure.backup import container
from personal_finance.infrastructure.persistence import migration_runner as migrations
from tests.conftest import BACKUP_PASSWORD, PASSWORD


@pytest.fixture(autouse=True)
def explicit_future_schema(monkeypatch):
    supported = tuple(dict.fromkeys((*migrations.SUPPORTED_SCHEMAS, "0014")))
    monkeypatch.setattr(migrations, "SCHEMA_HEAD", "0014")
    monkeypatch.setattr(migrations, "SUPPORTED_SCHEMAS", supported)
    monkeypatch.setattr(container, "SUPPORTED_SCHEMAS", supported)
    original = migrations.initialize_schema
    monkeypatch.setattr(vault, "initialize_schema", lambda engine: original(engine, target="0014"))


def at(monkeypatch, path, version):
    with monkeypatch.context() as patch:
        patch.setattr(
            vault,
            "initialize_schema",
            lambda engine: migrations.initialize_schema(engine, target=version),
        )
        return vault.create_vault(path, "Вымышленная книга", PASSWORD)


@pytest.mark.parametrize("version", tuple(f"{i:04d}" for i in range(2, 14)))
def test_old_books_open_without_ddl_restore_to14_only_new_folder(tmp_path, monkeypatch, version):
    root = tmp_path / "old.pfvault"
    s, _ = at(monkeypatch, root, version)
    try:
        account = s.catalog.add_account("Вымышленный счёт", AccountKind.CASH)
        original = (root / "data/key.json").read_bytes()
        archive = tmp_path / "synthetic.pfbackup"
        container.create_backup(s, archive, BACKUP_PASSWORD)
        before = sha256(archive.read_bytes()).hexdigest()
        assert s.schema_revision == version and s.bonuses is None
        s.close()
        old = vault.open_vault(root, PASSWORD)
        try:
            assert old.schema_revision == version and old.bonuses is None
            assert old.catalog.accounts()[0].id == account.id
            old.check_integrity()
        finally:
            old.close()
        restored, pf = container.restore_backup(
            archive, tmp_path / "new.pfvault", BACKUP_PASSWORD, PASSWORD
        )
        try:
            assert restored.schema_revision == "0014" and restored.bonuses is not None
            assert restored.deposit_scenarios is not None and restored.classification is not None
            assert (
                restored.ocr_importing.supports_universal
                and restored.ocr_importing.supports_batches
            )
            assert restored.catalog.accounts()[0].id == account.id
            p = restored.bonuses.save_program("Вымышленная программа")
            restored.bonuses.save_entry(
                BonusDraft(p.id, date(2026, 10, 31), BonusKind.ACCRUAL, 125)
            )
            restored.check_integrity()
        finally:
            restored.close()
        recovered = vault.open_vault(tmp_path / "new.pfvault", pf, recovery=True)
        try:
            assert recovered.bonuses.balances()[p.id] == 125
            recovered.check_integrity()
        finally:
            recovered.close()
        assert (root / "data/key.json").read_bytes() == original
        assert sha256(archive.read_bytes()).hexdigest() == before
    finally:
        s.close()


def test_new_wallet_backup_restore_preserves_points_history_and_money(tmp_path):
    root = tmp_path / "source.pfvault"
    s, pf = vault.create_vault(root, "Тест бонусов", PASSWORD)
    try:
        assert s.schema_revision == "0014"
        service = s.bonuses
        p = service.save_program("Программа")
        entry = service.save_entry(BonusDraft(p.id, date(2026, 10, 30), BonusKind.ACCRUAL, 150))
        service.save_entry(BonusDraft(p.id, date(2026, 10, 31), BonusKind.SPENDING, -25))
        before = (s.ledger.operations(), s.ledger.balances())
        key = (root / "data/key.json").read_bytes()
        archive = tmp_path / "backup.pfbackup"
        container.create_backup(s, archive, BACKUP_PASSWORD)
        container.verify_backup(archive, BACKUP_PASSWORD)
        restored, new_pf = container.restore_backup(
            archive, tmp_path / "new.pfvault", BACKUP_PASSWORD, PASSWORD
        )
        try:
            assert restored.bonuses.balances()[p.id] == 125
            assert restored.bonuses.history(entry.id) == [entry]
            assert (restored.ledger.operations(), restored.ledger.balances()) == before
            assert restored.replica_id != s.replica_id and new_pf != pf
            restored.check_integrity()
        finally:
            restored.close()
        assert (root / "data/key.json").read_bytes() == key
    finally:
        s.close()


def test_failed_migration_rolls_back_and_never_publishes_destination(tmp_path, monkeypatch):
    root = tmp_path / "source.pfvault"
    s, _ = at(monkeypatch, root, "0013")
    try:
        account = s.catalog.add_account("Исходный счёт", AccountKind.CASH)
        key = (root / "data/key.json").read_bytes()
        archive = tmp_path / "backup.pfbackup"
        container.create_backup(s, archive, BACKUP_PASSWORD)
        digest = sha256(archive.read_bytes()).hexdigest()
        from sqlalchemy.engine import Engine

        def fail(connection, cursor, statement, parameters, context, many):
            if "create table bonus_entry_revisions" in statement.lower():
                raise RuntimeError("Synthetic migration failure")

        event.listen(Engine, "before_cursor_execute", fail)
        try:
            with pytest.raises(RuntimeError, match="Synthetic migration failure"):
                container.restore_backup(
                    archive, tmp_path / "failed.pfvault", BACKUP_PASSWORD, PASSWORD
                )
        finally:
            event.remove(Engine, "before_cursor_execute", fail)
        assert not (tmp_path / "failed.pfvault").exists()
        assert s.schema_revision == "0013" and s.catalog.accounts()[0].id == account.id
        assert (root / "data/key.json").read_bytes() == key and sha256(
            archive.read_bytes()
        ).hexdigest() == digest
        s.check_integrity()
    finally:
        s.close()
