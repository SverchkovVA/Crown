"""Пробный цикл 0018 на временных зашифрованных книгах."""

import hashlib
from contextlib import closing
from dataclasses import replace
from datetime import timedelta
from uuid import uuid4

import pytest
from alembic import op

from personal_finance.application import vault
from personal_finance.domain.errors import InvalidCredentials, UnsupportedFormat
from personal_finance.domain.investments import StockSplit
from personal_finance.domain.ledger import OperationDraft, OperationKind
from personal_finance.domain.models import AccountKind, Money
from personal_finance.infrastructure.backup import container
from personal_finance.infrastructure.persistence import migration_runner as migrations
from tests.conftest import BACKUP_PASSWORD, PASSWORD
from tests.integration.test_deposit_scenario_upgrade import create_at
from tests.integration.test_planning_scenario_upgrade import hashes, state
from tests.unit.test_investment_positions import inputs
from tests.unit.test_investments import TODAY
from tests.unit.test_payment_calendar_codec import calendar

NEW_PASSWORD = "Вымышленный пароль восстановления с дроблением акций"
BASE_SCHEMAS = tuple(version for version in migrations.SUPPORTED_SCHEMAS if version < "0018")
TRIAL_SCHEMA = "0018"


@pytest.fixture
def split_schema(monkeypatch):
    supported = (*BASE_SCHEMAS, TRIAL_SCHEMA)
    for module in (migrations, container):
        monkeypatch.setattr(module, "SUPPORTED_SCHEMAS", supported)
        monkeypatch.setattr(module, "SCHEMA_HEAD", TRIAL_SCHEMA)
    monkeypatch.setattr(
        vault,
        "initialize_schema",
        lambda engine: migrations.initialize_schema(engine, target=TRIAL_SCHEMA),
    )


def restore(archive, target):
    return container.restore_backup(archive, target, BACKUP_PASSWORD, NEW_PASSWORD)


def immutable_copy(source, archive):
    return hashes(source), hashlib.sha256(archive.read_bytes()).hexdigest()


@pytest.mark.parametrize("version", BASE_SCHEMAS)
def test_older_book_restores_to_0018_only_in_new_folder(
    tmp_path, monkeypatch, split_schema, version
):
    source = tmp_path / "original.pfvault"
    session, old_pf = create_at(monkeypatch, source, version)
    with closing(session):
        assert session.schema_revision == version and not session.supports_stock_splits
        account = session.catalog.add_account("Учебный брокерский счёт", AccountKind.INVESTMENT)
        if session.ledger is not None:
            session.ledger.add(
                OperationDraft(OperationKind.OPENING, TODAY, account.id, Money(50000))
            )
        saved_calendar = (
            session.planning_scenarios.save("Учебное расписание", calendar())
            if session.supports_payment_calendars
            else None
        )
        position = (
            session.investment_positions.save(inputs(account))
            if session.investment_positions is not None
            else None
        )
        replica = session.replica_id
        expected = state(session)
        archive = container.create_backup(session, tmp_path / "original.pfbackup", BACKUP_PASSWORD)
    before = immutable_copy(source, archive)

    target = tmp_path / "restored.pfvault"
    restored, new_pf = restore(archive, target)
    with closing(restored):
        assert restored.schema_revision == TRIAL_SCHEMA and restored.supports_stock_splits
        assert restored.replica_id != replica and new_pf != old_pf
        actual = state(restored)
        assert all(actual[key] == value for key, value in expected.items())
        if saved_calendar is not None:
            assert restored.planning_scenarios.get(saved_calendar.id) == saved_calendar
        if position is not None:
            assert restored.investment_positions.history(position.id) == [position]
        restored.check_integrity()
    with closing(vault.open_vault(source, PASSWORD)) as reopened:
        assert reopened.schema_revision == version and not reopened.supports_stock_splits
    assert immutable_copy(source, archive) == before
    with closing(vault.open_vault(target, new_pf, recovery=True)) as recovered:
        assert recovered.schema_revision == TRIAL_SCHEMA


def test_format2_survives_reopen_and_backup_restore_with_receipts(
    tmp_path, monkeypatch, split_schema
):
    source = tmp_path / "split.pfvault"
    session, original_pf = vault.create_vault(source, "Учебная книга 0018", PASSWORD)
    first_command, split_command = str(uuid4()), str(uuid4())
    with closing(session):
        assert session.schema_revision == TRIAL_SCHEMA and session.supports_stock_splits
        account = session.catalog.add_account("Учебный инвестсчёт", AccountKind.INVESTMENT)
        prior = inputs(account)
        first = session.investment_positions.save(prior, command_id=first_command)
        split = StockSplit(
            str(uuid4()), account.id, prior.security.id, TODAY + timedelta(days=1), 1, 100
        )
        updated = replace(prior, splits=(split,))
        before = state(session)
        parameters = dict(
            identifier=first.id, expected_revision_id=first.revision_id, command_id=split_command
        )
        second = session.investment_positions.save(updated, **parameters)
        assert state(session) == before
        replica = session.replica_id
        archive = container.create_backup(session, tmp_path / "split.pfbackup", BACKUP_PASSWORD)
    original = immutable_copy(source, archive)

    with monkeypatch.context() as old:
        old.setattr(migrations, "SUPPORTED_SCHEMAS", BASE_SCHEMAS)
        old.setattr(container, "SUPPORTED_SCHEMAS", BASE_SCHEMAS)
        with pytest.raises(UnsupportedFormat):
            vault.open_vault(source, PASSWORD)
        with pytest.raises(UnsupportedFormat):
            container.verify_backup(archive, BACKUP_PASSWORD)
        forbidden = tmp_path / "not-restored.pfvault"
        with pytest.raises(UnsupportedFormat):
            restore(archive, forbidden)
        assert not forbidden.exists() and not list(tmp_path.glob(".pf-restore-*"))
    assert immutable_copy(source, archive) == original

    with closing(vault.open_vault(source, PASSWORD)) as reopened:
        assert reopened.investment_positions.history(first.id) == [first, second]
    assert container.verify_backup(archive, BACKUP_PASSWORD)["schema"] == TRIAL_SCHEMA
    contents = container.inspect_backup(archive, BACKUP_PASSWORD)
    assert (contents.source_schema, contents.restore_schema) == (TRIAL_SCHEMA, TRIAL_SCHEMA)
    assert (contents.accounts, contents.investment_positions, contents.operations) == (1, 1, 0)
    target = tmp_path / "restored.pfvault"
    restored, new_pf = restore(archive, target)
    with closing(restored):
        assert restored.replica_id != replica and new_pf != original_pf
        store = restored.investment_positions
        assert store.history(first.id) == [first, second]
        assert store.save(prior, command_id=first_command) == first
        assert store.save(updated, **parameters) == second
        assert state(restored) == before
        restored.check_integrity()
    with pytest.raises(InvalidCredentials):
        vault.open_vault(target, PASSWORD)
    with closing(vault.open_vault(target, new_pf, recovery=True)) as recovered:
        assert recovered.investment_positions.get(first.id) == second
    assert immutable_copy(source, archive) == original


@pytest.mark.parametrize(
    "prefix",
    [
        "INSERT INTO investment_position_revisions_0018",
        "DROP TABLE investment_position_revisions",
        "ALTER TABLE investment_position_revisions_0018",
    ],
)
def test_0018_restore_failure_leaves_no_published_book(tmp_path, monkeypatch, split_schema, prefix):
    source = tmp_path / "original.pfvault"
    session, _ = create_at(monkeypatch, source, "0017")
    with closing(session):
        account = session.catalog.add_account("Учебный инвестсчёт", AccountKind.INVESTMENT)
        position = session.investment_positions.save(inputs(account))
        archive = container.create_backup(session, tmp_path / "original.pfbackup", BACKUP_PASSWORD)
    before = immutable_copy(source, archive)
    execute = op.execute

    def fail(sql, *args, **kwargs):
        result = execute(sql, *args, **kwargs)
        if isinstance(sql, str) and sql.startswith(prefix):
            raise OSError("Синтетический отказ переноса инвестиционных версий")
        return result

    target = tmp_path / "failed.pfvault"
    with monkeypatch.context() as patch:
        patch.setattr(op, "execute", fail)
        with pytest.raises(OSError, match="Синтетический отказ"):
            restore(archive, target)
    assert not target.exists() and not list(tmp_path.glob(".pf-restore-*"))
    assert immutable_copy(source, archive) == before
    with closing(vault.open_vault(source, PASSWORD)) as reopened:
        assert reopened.schema_revision == "0017"
        assert reopened.investment_positions.get(position.id) == position
