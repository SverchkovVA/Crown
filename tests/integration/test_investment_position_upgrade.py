"""Сквозная проверка 0017 только во временной цепочке Alembic."""

import hashlib
import shutil
from contextlib import closing
from pathlib import Path
from uuid import uuid4

import pytest
from alembic import op

from personal_finance.application import vault
from personal_finance.domain.errors import InvalidCredentials, UnsupportedFormat
from personal_finance.domain.ledger import OperationDraft, OperationKind
from personal_finance.domain.models import AccountKind, Money
from personal_finance.infrastructure.backup import container
from personal_finance.infrastructure.persistence import migration_runner as migrations
from personal_finance.infrastructure.persistence.migrations.candidates import (
    v0017_investment_positions,
)
from tests.conftest import BACKUP_PASSWORD, PASSWORD
from tests.integration.test_deposit_scenario_upgrade import create_at
from tests.integration.test_planning_scenario_upgrade import hashes, state
from tests.unit.test_investment_positions import inputs
from tests.unit.test_investments import TODAY
from tests.unit.test_payment_calendar_codec import calendar

NEW_PASSWORD = "Вымышленный пароль восстановления инвестиционных позиций"
BASE_SCHEMAS = migrations.SUPPORTED_SCHEMAS


@pytest.fixture
def candidate_schema(tmp_path, monkeypatch):
    assert migrations.SCHEMA_HEAD == "0016" and "0017" not in migrations.SUPPORTED_SCHEMAS
    runtime = tmp_path / "candidate-migrations"
    path = Path(migrations.__file__).with_name("migrations")
    shutil.copytree(path, runtime, ignore=shutil.ignore_patterns("__pycache__"))
    shutil.copyfile(
        Path(v0017_investment_positions.__file__),
        runtime / "versions/v0017_investment_positions.py",
    )
    configuration = migrations._configuration

    def test_configuration():
        config = configuration()
        config.set_main_option("script_location", str(runtime))
        return config

    monkeypatch.setattr(migrations, "_configuration", test_configuration)
    monkeypatch.setattr(migrations, "SCHEMA_HEAD", "0017")
    monkeypatch.setattr(migrations, "SUPPORTED_SCHEMAS", (*BASE_SCHEMAS, "0017"))
    monkeypatch.setattr(container, "SUPPORTED_SCHEMAS", (*BASE_SCHEMAS, "0017"))
    monkeypatch.setattr(
        vault,
        "initialize_schema",
        lambda engine: migrations.initialize_schema(engine, target="0017"),
    )


def restore(archive, target):
    return container.restore_backup(archive, target, BACKUP_PASSWORD, NEW_PASSWORD)


@pytest.mark.parametrize("version", BASE_SCHEMAS)
def test_old_book_restores_to_candidate_only_in_new_folder(
    candidate_schema, tmp_path, monkeypatch, version
):
    source = tmp_path / "original.pfvault"
    session, _ = create_at(monkeypatch, source, version)
    try:
        assert session.schema_revision == version and session.investment_positions is None
        account = session.catalog.add_account("Синтетический инвестсчёт", AccountKind.INVESTMENT)
        if session.ledger is not None:
            session.ledger.add(
                OperationDraft(OperationKind.OPENING, TODAY, account.id, Money(50000))
            )
        saved_calendar = None
        if session.supports_payment_calendars:
            saved_calendar = session.planning_scenarios.save("Синтетическое расписание", calendar())
        expected = state(session)
        archive = container.create_backup(session, tmp_path / "original.pfbackup", BACKUP_PASSWORD)
    finally:
        session.close()
    original = hashes(source), hashlib.sha256(archive.read_bytes()).hexdigest()
    restored, _ = restore(archive, tmp_path / "restored.pfvault")
    try:
        assert restored.schema_revision == "0017" and restored.supports_payment_calendars
        actual = state(restored)
        assert all(actual[key] == value for key, value in expected.items())
        if saved_calendar is not None:
            assert restored.planning_scenarios.get(saved_calendar.id) == saved_calendar
        store = restored.investment_positions
        saved = store.save(inputs(account))
        assert store.get(saved.id) == saved
        restored.check_integrity()
    finally:
        restored.close()
    assert original == (hashes(source), hashlib.sha256(archive.read_bytes()).hexdigest())
    with closing(vault.open_vault(source, PASSWORD)) as reopened:
        assert reopened.schema_revision == version and reopened.investment_positions is None


def test_candidate_preserves_versions_receipts_and_rejects_an_older_reader(
    candidate_schema, tmp_path, monkeypatch
):
    path = tmp_path / "positions.pfvault"
    source, old_pf = vault.create_vault(path, "Синтетическая книга 0017", PASSWORD)
    command = str(uuid4())
    try:
        assert source.schema_revision == "0017"
        account = source.catalog.add_account("Учебный счёт", AccountKind.INVESTMENT)
        first = source.investment_positions.save(inputs(account), command_id=command)
        second = source.investment_positions.save(
            first.inputs, identifier=first.id, expected_revision_id=first.revision_id
        )
        expected = state(source)
        source.check_integrity()
        archive = container.create_backup(source, tmp_path / "positions.pfbackup", BACKUP_PASSWORD)
        assert container.verify_backup(archive, BACKUP_PASSWORD)["schema"] == "0017"
        replica = source.replica_id
    finally:
        source.close()
    source_before = hashes(path)
    with monkeypatch.context() as old_reader:
        old_reader.setattr(migrations, "SUPPORTED_SCHEMAS", BASE_SCHEMAS)
        with pytest.raises(UnsupportedFormat):
            vault.open_vault(path, PASSWORD)
    assert hashes(path) == source_before
    target = tmp_path / "copy.pfvault"
    restored, new_pf = restore(archive, target)
    try:
        assert (
            restored.schema_revision == "0017"
            and restored.replica_id != replica
            and new_pf != old_pf
        )
        assert restored.investment_positions.history(first.id) == [first, second]
        assert restored.investment_positions.save(first.inputs, command_id=command) == first
        assert state(restored) == expected
        restored.check_integrity()
    finally:
        restored.close()
    assert hashes(path) == source_before
    with pytest.raises(InvalidCredentials):
        vault.open_vault(target, PASSWORD)
    with closing(vault.open_vault(target, new_pf, recovery=True)) as opened:
        assert opened.investment_positions.get(first.id) == second


@pytest.mark.parametrize(
    "prefix",
    [
        'CREATE TABLE "entities_0017"',
        "CREATE TABLE investment_position_revisions",
        "CREATE TRIGGER investment_position_unique_active",
    ],
)
def test_candidate_restore_fault_never_publishes(candidate_schema, tmp_path, monkeypatch, prefix):
    source = tmp_path / "original.pfvault"
    session, _ = create_at(monkeypatch, source, "0016")
    try:
        session.catalog.add_account("Учебный инвестсчёт", AccountKind.INVESTMENT)
        archive = container.create_backup(session, tmp_path / "original.pfbackup", BACKUP_PASSWORD)
    finally:
        session.close()
    before = hashes(source), hashlib.sha256(archive.read_bytes()).hexdigest()
    execute = op.execute

    def fail(sql, *args, **kwargs):
        result = execute(sql, *args, **kwargs)
        if isinstance(sql, str) and sql.startswith(prefix):
            raise OSError("Синтетический отказ инвестиционной миграции")
        return result

    target = tmp_path / "failed.pfvault"
    with monkeypatch.context() as patch:
        patch.setattr(op, "execute", fail)
        with pytest.raises(OSError, match="Синтетический отказ"):
            restore(archive, target)
    assert not target.exists()
    assert not list(tmp_path.glob(".pf-restore-*"))
    assert before == (hashes(source), hashlib.sha256(archive.read_bytes()).hexdigest())
