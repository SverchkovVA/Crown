"""Создание книги 0015 и восстановление 0002–0015 в новую папку."""

import hashlib
from datetime import date
from uuid import uuid4

import pytest
from alembic import op

from personal_finance.application import vault
from personal_finance.domain.analytics import ReportPeriod
from personal_finance.domain.bonuses import BonusDraft, BonusKind
from personal_finance.domain.errors import InvalidCredentials, VaultError
from personal_finance.domain.ledger import OperationDraft, OperationKind
from personal_finance.domain.models import AccountKind, Money
from personal_finance.domain.planning_scenarios import BudgetScenario
from personal_finance.infrastructure.backup import container
from personal_finance.infrastructure.persistence import migration_runner
from tests.conftest import BACKUP_PASSWORD, PASSWORD
from tests.integration.test_deposit_scenario_upgrade import create_at
from tests.integration.test_generic_ocr_migration import populate
from tests.integration.test_planning_scenario_migration import snapshot
from tests.unit.test_deposits import scenario as deposit
from tests.unit.test_planning_scenarios import goal

NEW_PASSWORD = "Синтетический новый пароль для явной пятнадцатой схемы"


def hashes(root):
    return tuple(
        sorted(
            (p.relative_to(root).as_posix(), hashlib.sha256(p.read_bytes()).hexdigest())
            for p in root.rglob("*")
            if p.is_file()
        )
    )


def restore15(monkeypatch, backup, target):
    with monkeypatch.context() as patch:
        patch.setattr(migration_runner, "SCHEMA_HEAD", "0015")
        return container.restore_backup(backup, target, BACKUP_PASSWORD, NEW_PASSWORD)


def state(session):
    result = {
        "catalog": (
            session.catalog.institutions(),
            session.catalog.accounts(),
            session.catalog.categories(),
        )
    }
    if session.ledger is not None:
        result["money"] = session.ledger.operations(include_deleted=True), session.ledger.balances()
    if session.deposit_scenarios is not None:
        result["deposits"] = session.deposit_scenarios.items(include_archived=True)
    if session.bonuses is not None:
        result["bonuses"] = (
            session.bonuses.programs(include_archived=True),
            session.bonuses.entries(include_deleted=True),
        )
    return result


def budget():
    return BudgetScenario(ReportPeriod(date(2026, 1, 1), date(2026, 1, 31)), Money(12345))


def test_explicit15_roundtrip_retains_versions_commands_keys_and_money(tmp_path, monkeypatch):
    source = tmp_path / "default15.pfvault"
    session, old_pf = create_at(monkeypatch, source, "0015")
    try:
        assert session.schema_revision == "0015"
        assert all(
            getattr(session, name) is not None
            for name in (
                "ledger",
                "importing",
                "exporting",
                "periods",
                "organization",
                "classification",
                "ocr_importing",
                "ocr_settings",
                "deposit_scenarios",
                "bonuses",
                "planning_scenarios",
            )
        )
        assert session.ocr_importing.supports_universal and session.ocr_importing.supports_batches
        service = session.planning_scenarios
        command = str(uuid4())
        first = service.save("Синтетическая цель", goal(), command_id=command)
        revised = service.rename(first.id, first.revision_id, "Переименованная цель")
        archived = service.archive(first.id, revised.revision_id)
        saved_budget = service.save("Синтетический бюджет", budget())
        before = state(session)
        archive = container.create_backup(session, tmp_path / "source15.pfbackup", BACKUP_PASSWORD)
        assert container.verify_backup(archive, BACKUP_PASSWORD)["schema"] == "0015"
        replica, database_key = session.replica_id, session.backup_key()
    finally:
        session.close()
    source_hash, backup_hash = hashes(source), hashlib.sha256(archive.read_bytes()).hexdigest()
    restored, pf = restore15(monkeypatch, archive, tmp_path / "restored15.pfvault")
    try:
        assert (
            restored.replica_id != replica
            and restored.backup_key() == database_key
            and pf != old_pf
        )
        assert state(restored) == before
        assert restored.planning_scenarios.history(first.id) == [first, revised, archived]
        assert restored.planning_scenarios.get(saved_budget.id) == saved_budget
        assert (
            restored.planning_scenarios.save("Синтетическая цель", goal(), command_id=command)
            == first
        )
        restored.check_integrity()
        target = restored.root
    finally:
        restored.close()
    assert (
        hashes(source) == source_hash
        and hashlib.sha256(archive.read_bytes()).hexdigest() == backup_hash
    )
    for secret, recovery in ((PASSWORD, False), (old_pf, True)):
        with pytest.raises(InvalidCredentials):
            vault.open_vault(target, secret, recovery=recovery)
    for secret, recovery in ((NEW_PASSWORD, False), (pf, True)):
        opened = vault.open_vault(target, secret, recovery=recovery)
        try:
            assert opened.planning_scenarios.history(first.id) == [first, revised, archived]
            opened.check_integrity()
        finally:
            opened.close()


@pytest.mark.parametrize("version", tuple(f"{n:04d}" for n in range(2, 15)))
def test_each_old_schema_opens_unchanged_and_restores_only_into_new15(
    tmp_path, monkeypatch, version
):
    source = tmp_path / "old.pfvault"
    session, _ = create_at(monkeypatch, source, version)
    try:
        account = session.catalog.add_account("Синтетический счёт", AccountKind.CASH)
        if session.ledger is not None:
            session.ledger.add(
                OperationDraft(OperationKind.OPENING, date(2026, 1, 1), account.id, Money(10000))
            )
        if session.deposit_scenarios is not None:
            session.deposit_scenarios.save("Синтетический вклад", deposit())
        if session.bonuses is not None:
            program = session.bonuses.save_program("Синтетическая программа")
            session.bonuses.save_entry(
                BonusDraft(program.id, date(2026, 1, 2), BonusKind.ACCRUAL, 100)
            )
        before = state(session)
        replica, database_key = session.replica_id, session.backup_key()
        archive = container.create_backup(session, tmp_path / "old.pfbackup", BACKUP_PASSWORD)
        assert container.verify_backup(archive, BACKUP_PASSWORD)["schema"] == version
    finally:
        session.close()
    source_hash, backup_hash = hashes(source), hashlib.sha256(archive.read_bytes()).hexdigest()
    old = vault.open_vault(source, PASSWORD)
    try:
        assert old.schema_revision == version and old.planning_scenarios is None
        assert state(old) == before
        old.check_integrity()
    finally:
        old.close()
    assert hashes(source) == source_hash
    restored, pf = restore15(monkeypatch, archive, tmp_path / "new15.pfvault")
    try:
        assert restored.schema_revision == "0015" and restored.planning_scenarios is not None
        assert restored.replica_id != replica and restored.backup_key() == database_key
        assert (
            hashlib.sha256((restored.root / "data/key.json").read_bytes()).hexdigest()
            != dict(source_hash)["data/key.json"]
        )
        after = state(restored)
        assert all(after[key] == value for key, value in before.items())
        saved = restored.planning_scenarios.save("Цель после восстановления", goal())
        saved_budget = restored.planning_scenarios.save("Бюджет после восстановления", budget())
        restored.check_integrity()
        target = restored.root
    finally:
        restored.close()
    for secret, recovery in ((NEW_PASSWORD, False), (pf, True)):
        opened = vault.open_vault(target, secret, recovery=recovery)
        try:
            assert opened.planning_scenarios.get(saved.id) == saved
            assert opened.planning_scenarios.get(saved_budget.id) == saved_budget
            opened.check_integrity()
        finally:
            opened.close()
    with pytest.raises(InvalidCredentials):
        vault.open_vault(target, PASSWORD)
    assert (
        hashes(source) == source_hash
        and hashlib.sha256(archive.read_bytes()).hexdigest() == backup_hash
    )


def test_restore14_preserves_every_legacy_row_and_guard_before_saving_conditions(
    tmp_path, monkeypatch
):
    source = tmp_path / "rich14.pfvault"
    session, _ = create_at(monkeypatch, source, "0014")
    try:
        populate(session)
        session.organization.add_tag("Синтетический тег")
        session.deposit_scenarios.save("Синтетический вклад", deposit())
        program = session.bonuses.save_program("Синтетические баллы")
        session.bonuses.save_entry(BonusDraft(program.id, date(2026, 1, 2), BonusKind.ACCRUAL, 100))
        before = snapshot(session.engine)
        archive = container.create_backup(session, tmp_path / "rich14.pfbackup", BACKUP_PASSWORD)
    finally:
        session.close()
    source_hash = hashes(source)
    restored, _ = restore15(monkeypatch, archive, tmp_path / "rich15.pfvault")
    try:
        after = snapshot(restored.engine)
        for table, rows in before[1].items():
            if table == "replicas":
                assert set(rows) < set(after[1][table])
            elif table not in {"alembic_version", "local_state"}:
                assert rows == after[1][table], table
        original = {row[1]: row for row in before[0]}
        current = {row[1]: row for row in after[0]}
        for name, row in original.items():
            if name not in {"entities", "revision_payload_valid"}:
                assert current[name] == row, name
        assert after[1]["planning_scenario_revisions"] == []
        restored.check_integrity()
    finally:
        restored.close()
    assert hashes(source) == source_hash


@pytest.mark.parametrize(
    "prefix",
    (
        'CREATE TABLE "entities_0015"',
        "INSERT INTO entities_0015",
        "DROP TABLE entities",
        "ALTER TABLE entities_0015",
        "CREATE TABLE planning_scenario_revisions",
        "CREATE INDEX ix_planning_scenario_name",
        "CREATE TRIGGER planning_scenario_type_immutable",
    ),
)
def test_restore_failure_never_publishes_or_changes_source(tmp_path, monkeypatch, prefix):
    source = tmp_path / "source14.pfvault"
    session, _ = create_at(monkeypatch, source, "0014")
    try:
        session.catalog.add_account("Исходный синтетический счёт", AccountKind.CASH)
        archive = container.create_backup(session, tmp_path / "failure.pfbackup", BACKUP_PASSWORD)
    finally:
        session.close()
    source_hash, backup_hash = hashes(source), hashlib.sha256(archive.read_bytes()).hexdigest()
    execute = op.execute

    def fail(statement, *args, **kwargs):
        result = execute(statement, *args, **kwargs)
        if str(statement).startswith(prefix):
            raise OSError("Синтетический сбой кандидата 0015")
        return result

    with monkeypatch.context() as patch:
        patch.setattr(op, "execute", fail)
        with pytest.raises(OSError, match="Синтетический сбой"):
            restore15(monkeypatch, archive, tmp_path / "failed.pfvault")
    assert not (tmp_path / "failed.pfvault").exists()
    assert not list(tmp_path.glob(".pf-restore-*"))
    assert (
        hashes(source) == source_hash
        and hashlib.sha256(archive.read_bytes()).hexdigest() == backup_hash
    )
    reopened = vault.open_vault(source, PASSWORD)
    try:
        assert reopened.schema_revision == "0014" and reopened.planning_scenarios is None
        reopened.check_integrity()
    finally:
        reopened.close()


def test_vault_integrity_and_backup_reject_corrupted_planning_payload(tmp_path, monkeypatch):
    session, _ = create_at(monkeypatch, tmp_path / "corrupted15.pfvault", "0015")
    try:
        saved = session.planning_scenarios.save("Синтетическая цель", goal())
        with session.engine.begin() as connection:
            connection.exec_driver_sql("DROP TRIGGER planning_scenario_no_update")
            connection.exec_driver_sql(
                "UPDATE planning_scenario_revisions SET name=? WHERE revision_id=?",
                ("Имя с несогласованным ключом", saved.revision_id),
            )
        with pytest.raises(VaultError):
            session.check_integrity()
        destination = tmp_path / "corrupted.pfbackup"
        with pytest.raises(VaultError):
            container.create_backup(session, destination, BACKUP_PASSWORD)
        assert not destination.exists()
    finally:
        session.close()
