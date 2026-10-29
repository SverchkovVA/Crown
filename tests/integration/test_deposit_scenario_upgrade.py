"""Явная 0013 проверяется на новых временных книгах до смены SCHEMA_HEAD."""

import hashlib
from dataclasses import replace
from datetime import date
from uuid import uuid4

import pytest
from alembic import op

from personal_finance.application import vault
from personal_finance.domain.errors import InvalidCredentials, UnsupportedFormat, VaultError
from personal_finance.domain.ledger import OperationDraft, OperationKind
from personal_finance.domain.models import AccountKind, CategoryKind, Money
from personal_finance.infrastructure.backup.container import (
    create_backup,
    restore_backup,
    verify_backup,
)
from personal_finance.infrastructure.persistence import migration_runner
from tests.conftest import BACKUP_PASSWORD, PASSWORD
from tests.unit.test_deposits import scenario

NEW_PASSWORD = "Вымышленный новый пароль только для схемы тринадцать"
OLD_SCHEMAS = tuple(f"{n:04d}" for n in range(2, 13))


def create_at(monkeypatch, path, version):
    with monkeypatch.context() as patch:
        patch.setattr(
            vault,
            "initialize_schema",
            lambda engine: migration_runner.initialize_schema(engine, target=version),
        )
        return vault.create_vault(path, "Вымышленная проверка схемы", PASSWORD)


def hashes(root):
    return tuple(
        hashlib.sha256((root / "data" / name).read_bytes()).hexdigest()
        for name in ["ledger.sqlite3", "key.json"]
    )


def restore_at_13(monkeypatch, backup, target):
    with monkeypatch.context() as patch:
        patch.setattr(migration_runner, "SCHEMA_HEAD", "0013")
        return restore_backup(backup, target, BACKUP_PASSWORD, NEW_PASSWORD)


def test_explicit_new13_has_all_capabilities_and_scenario_versions(tmp_path, monkeypatch):
    session, _ = create_at(monkeypatch, tmp_path / "explicit13.pfvault", "0013")
    try:
        assert session.schema_revision == "0013"
        assert (
            session.ledger.supports_splits
            and session.ledger.supports_refunds
            and session.ledger.supports_tags
        )
        assert all(
            v is not None
            for v in [
                session.importing,
                session.exporting,
                session.periods,
                session.organization,
                session.classification,
                session.ocr_settings,
                session.deposit_scenarios,
            ]
        )
        assert session.ocr_importing.supports_batches and session.ocr_importing.supports_universal
        before = session.ledger.operations(), session.ledger.balances()
        service = session.deposit_scenarios
        command = str(uuid4())
        first = service.save("Исходный сценарий", scenario(), command_id=command)
        second = service.save(
            "Изменённые условия",
            replace(scenario(), initial=Money(200002)),
            identifier=first.id,
            expected_revision_id=first.revision_id,
        )
        assert service.save("Исходный сценарий", scenario(), command_id=command) == first
        archived = service.archive(first.id, second.revision_id)
        assert service.items() == [] and service.history(first.id) == [first, second, archived]
        service.archive(first.id, archived.revision_id, archived=False)
        assert (session.ledger.operations(), session.ledger.balances()) == before
        session.check_integrity()
    finally:
        session.close()
    with pytest.raises(VaultError):
        service.items()


@pytest.mark.parametrize("version", OLD_SCHEMAS)
def test_every_old_schema_reopens_unchanged_and_restores_to_new13(tmp_path, monkeypatch, version):
    root = tmp_path / "old.pfvault"
    original, _ = create_at(monkeypatch, root, version)
    try:
        original.catalog.add_institution("Синтетический старый банк")
        account = original.catalog.add_account("Синтетический старый счёт", AccountKind.CASH)
        if original.ledger is not None:
            expense = next(
                v.id for v in original.catalog.categories() if v.kind == CategoryKind.EXPENSE
            )
            original.ledger.add(
                OperationDraft(OperationKind.OPENING, date(2026, 1, 1), account.id, Money(100000))
            )
            original.ledger.add(
                OperationDraft(
                    OperationKind.EXPENSE, date(2026, 1, 15), account.id, Money(10000), expense
                )
            )
            finances = original.ledger.operations(include_deleted=True), original.ledger.balances()
        else:
            finances = None
        catalog = (
            original.catalog.institutions(),
            original.catalog.accounts(),
            original.catalog.categories(),
        )
        replica = original.replica_id
        old_database_key = original.backup_key()
        backup = create_backup(original, tmp_path / "old.pfbackup", BACKUP_PASSWORD)
        assert verify_backup(backup, BACKUP_PASSWORD)["schema"] == version
    finally:
        original.close()
    source_hash = hashes(root)
    backup_hash = hashlib.sha256(backup.read_bytes()).hexdigest()
    reopened = vault.open_vault(root, PASSWORD)
    try:
        assert reopened.schema_revision == version and reopened.deposit_scenarios is None
    finally:
        reopened.close()
    assert hashes(root) == source_hash
    restored, recovery = restore_at_13(monkeypatch, backup, tmp_path / "restored13.pfvault")
    new_root = restored.root
    try:
        assert restored.schema_revision == "0013" and restored.deposit_scenarios is not None
        assert restored.replica_id != replica
        assert restored.backup_key() == old_database_key
        assert hashes(new_root)[1] != source_hash[1]
        assert (
            restored.catalog.institutions(),
            restored.catalog.accounts(),
            restored.catalog.categories(),
        ) == catalog
        if finances is not None:
            assert (
                restored.ledger.operations(include_deleted=True),
                restored.ledger.balances(),
            ) == finances
        saved = restored.deposit_scenarios.save("После старой копии", scenario())
        assert restored.deposit_scenarios.get(saved.id) == saved
        restored.check_integrity()
    finally:
        restored.close()
    assert (
        hashes(root) == source_hash
        and hashlib.sha256(backup.read_bytes()).hexdigest() == backup_hash
    )
    with pytest.raises(InvalidCredentials):
        vault.open_vault(new_root, PASSWORD)
    by_password = vault.open_vault(new_root, NEW_PASSWORD)
    try:
        assert by_password.deposit_scenarios.get(saved.id) == saved
    finally:
        by_password.close()
    by_recovery = vault.open_vault(new_root, recovery, recovery=True)
    try:
        by_recovery.check_integrity()
    finally:
        by_recovery.close()


def test_schema13_backup_keeps_history_and_command_under_new_identity(tmp_path, monkeypatch):
    original, _ = create_at(monkeypatch, tmp_path / "source13.pfvault", "0013")
    try:
        command = str(uuid4())
        first = original.deposit_scenarios.save("Начальные условия", scenario(), command_id=command)
        second = original.deposit_scenarios.rename(first.id, first.revision_id, "Новое название")
        replica = original.replica_id
        backup = create_backup(original, tmp_path / "source13.pfbackup", BACKUP_PASSWORD)
        assert verify_backup(backup, BACKUP_PASSWORD)["schema"] == "0013"
    finally:
        original.close()
    before = hashes(original.root)
    backup_hash = hashlib.sha256(backup.read_bytes()).hexdigest()
    restored, _ = restore_at_13(monkeypatch, backup, tmp_path / "restored13.pfvault")
    try:
        assert restored.replica_id != replica
        assert restored.deposit_scenarios.history(first.id) == [first, second]
        assert (
            restored.deposit_scenarios.save("Начальные условия", scenario(), command_id=command)
            == first
        )
        third = restored.deposit_scenarios.archive(first.id, second.revision_id)
        assert restored.deposit_scenarios.history(first.id) == [first, second, third]
        restored.check_integrity()
    finally:
        restored.close()
    assert (
        hashes(original.root) == before
        and hashlib.sha256(backup.read_bytes()).hexdigest() == backup_hash
    )


@pytest.mark.parametrize(
    "prefix",
    [
        'CREATE TABLE "entities_0013"',
        "DROP TABLE entities",
        "ALTER TABLE entities_0013",
        "CREATE TABLE deposit_scenario_revisions",
        "CREATE TRIGGER deposit_scenario_count_valid",
    ],
)
def test_restore_migration_failure_never_publishes_or_changes_source(tmp_path, monkeypatch, prefix):
    original, _ = create_at(monkeypatch, tmp_path / "source12.pfvault", "0012")
    try:
        original.catalog.add_account("Старый счёт", AccountKind.CASH)
        backup = create_backup(original, tmp_path / "source12.pfbackup", BACKUP_PASSWORD)
    finally:
        original.close()
    before = hashes(original.root)
    backup_hash = hashlib.sha256(backup.read_bytes()).hexdigest()
    execute = op.execute
    calls = []

    def fail(statement, *args, **kwargs):
        result = execute(statement, *args, **kwargs)
        if str(statement).startswith(prefix):
            calls.append(True)
            raise OSError("Синтетический отказ перехода 0013")
        return result

    monkeypatch.setattr(op, "execute", fail)
    target = tmp_path / "must-not-exist.pfvault"
    with pytest.raises(OSError):
        restore_at_13(monkeypatch, backup, target)
    assert calls == [True] and not target.exists()
    assert not list(tmp_path.glob(".pf-restore-*"))
    assert (
        hashes(original.root) == before
        and hashlib.sha256(backup.read_bytes()).hexdigest() == backup_hash
    )
    reopened = vault.open_vault(original.root, PASSWORD)
    try:
        reopened.check_integrity()
    finally:
        reopened.close()


def test_previous_reader_refuses13_without_changing_files(tmp_path, monkeypatch):
    session, _ = create_at(monkeypatch, tmp_path / "future13.pfvault", "0013")
    session.close()
    before = hashes(session.root)
    monkeypatch.setattr(migration_runner, "SUPPORTED_SCHEMAS", OLD_SCHEMAS)
    with pytest.raises(UnsupportedFormat):
        vault.open_vault(session.root, PASSWORD)
    assert hashes(session.root) == before


def test_integrity_checks_scenario_payload_through_vault(tmp_path, monkeypatch):
    session, _ = create_at(monkeypatch, tmp_path / "integrity13.pfvault", "0013")
    try:
        saved = session.deposit_scenarios.save("Сценарий", scenario())
        with session.engine.begin() as connection:
            connection.exec_driver_sql("DROP TRIGGER deposit_scenario_no_update")
            connection.exec_driver_sql(
                "UPDATE deposit_scenario_revisions SET name=?,name_key=? WHERE revision_id=?",
                ("Подмена", "подмена", saved.revision_id),
            )
        with pytest.raises(VaultError):
            session.check_integrity()
    finally:
        session.close()


def test_default_new_book_uses13(tmp_path):
    session, _ = vault.create_vault(
        tmp_path / "default13.pfvault", "Новая вымышленная книга", PASSWORD
    )
    try:
        assert session.schema_revision == "0013" and session.deposit_scenarios is not None
        assert session.deposit_scenarios.items() == [] and session.catalog.institutions() == []
        session.check_integrity()
    finally:
        session.close()
