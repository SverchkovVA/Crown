"""Изолированная квалификация формата 2 и перехода 0017 → 0018."""

import json
import shutil
from contextlib import closing
from dataclasses import replace
from uuid import uuid4

import pytest
from alembic import command, op
from alembic.runtime.migration import MigrationContext
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from personal_finance.application.vault import open_vault
from personal_finance.domain.errors import UnsupportedFormat, ValidationError, VaultError
from personal_finance.domain.investment_positions import (
    investment_position_from_json,
    investment_position_json,
)
from personal_finance.domain.investments import StockSplit
from personal_finance.domain.models import AccountKind
from personal_finance.infrastructure.persistence import migration_runner
from personal_finance.infrastructure.persistence.database import make_engine
from personal_finance.infrastructure.persistence.investment_position_repository import (
    SqlInvestmentPositions,
)
from tests.conftest import PASSWORD
from tests.integration.test_planning_scenario_upgrade import hashes
from tests.unit.test_investment_positions import inputs
from tests.unit.test_investments import TODAY


def with_split(value):
    return replace(
        value,
        splits=(StockSplit(str(uuid4()), value.account_id, value.security.id, TODAY, 3, 2),),
    )


def upgrade(engine):
    config = migration_runner._configuration()
    with engine.begin() as connection:
        config.attributes["connection"] = connection
        command.upgrade(config, "0018")


def snapshot(engine):
    with engine.connect() as connection:
        return (
            MigrationContext.configure(connection).get_current_heads(),
            tuple(connection.exec_driver_sql("SELECT * FROM investment_position_revisions")),
            tuple(
                connection.exec_driver_sql(
                    "SELECT type,name,sql FROM sqlite_master ORDER BY type,name"
                )
            ),
        )


def test_format2_requires_explicit_codec_and_preserves_legacy_json():
    legacy = inputs()
    raw = investment_position_json(legacy)
    assert investment_position_json(legacy, allow_corporate_events=True) == raw
    assert investment_position_from_json(raw, allow_corporate_events=True) == legacy
    candidate = with_split(legacy)
    with pytest.raises(ValidationError, match="совместимая"):
        investment_position_json(candidate)
    extended = investment_position_json(candidate, allow_corporate_events=True)
    decoded = json.loads(extended)
    assert decoded["format"] == 2 and decoded["splits"][0]["multiplier"] == 2
    assert investment_position_from_json(extended, allow_corporate_events=True) == candidate
    with pytest.raises(ValidationError, match="формат"):
        investment_position_from_json(extended)
    for change in (
        extended.replace('"multiplier":2', '"multiplier":true'),
        extended.replace('"multiplier":2', '"multiplier":2.0'),
        extended.replace('"format":2', '"format":1'),
        extended + " ",
    ):
        with pytest.raises(ValidationError, match="формат"):
            investment_position_from_json(change, allow_corporate_events=True)


def test_staged_update_keeps_old_receipts_and_integrity_and_accepts_new_revision(
    session_0017, tmp_path, monkeypatch
):
    session = session_0017
    # A full copy of this temporary test book is taken with the session closed.
    assert session.schema_revision == "0017" and not session.supports_corporate_events
    account = session.catalog.add_account("Учебный брокерский счёт", AccountKind.INVESTMENT)
    source = session.root
    command_id = str(uuid4())
    stock = inputs(account)
    record = session.investment_positions.save(stock, command_id=command_id)
    split = with_split(stock)
    with pytest.raises(ValidationError, match="совместимая"):
        session.investment_positions.save(
            split, identifier=record.id, expected_revision_id=record.revision_id
        )
    forced = SqlInvestmentPositions(
        session.engine,
        session.book_id,
        session.replica_id,
        session.mutex,
        allow_corporate_events=True,
    )
    with pytest.raises(IntegrityError, match="CHECK constraint"):
        forced.save(split, identifier=record.id, expected_revision_id=record.revision_id)
    assert session.investment_positions.history(record.id) == [record]
    key, identity, replica = session.backup_key(), session.book_id, session.replica_id
    before = snapshot(session.engine)
    session.close()
    baseline = hashes(source)
    target = tmp_path / "staged.pfvault"
    shutil.copytree(source, target)
    engine = make_engine(target / "data/ledger.sqlite3", key)
    try:
        upgrade(engine)
        heads, rows, _ = snapshot(engine)
        assert heads == ("0018",)
        assert rows == before[1]
        repo = SqlInvestmentPositions(
            engine, identity, replica, session.mutex, allow_corporate_events=True
        )
        assert repo.history(record.id) == [record]
        assert repo.save(stock, command_id=command_id) == record
        revised = repo.save(split, identifier=record.id, expected_revision_id=record.revision_id)
        assert revised.inputs == split and revised.id == record.id
        assert split.check_for(account).positions[0].quantity == 4
        assert repo.history(record.id) == [record, revised]
        old_repo = SqlInvestmentPositions(engine, identity, replica, session.mutex)
        with pytest.raises(VaultError, match="целостность"):
            old_repo.get(record.id)
        repo.check_integrity()
        with engine.connect() as connection, pytest.raises(IntegrityError, match="immutable"):
            connection.execute(
                text("UPDATE investment_position_revisions SET position_json=position_json")
            )
    finally:
        engine.dispose()
    assert hashes(source) == baseline
    target_before = hashes(target)
    with monkeypatch.context() as old_reader:
        prior_schemas = tuple(
            value for value in migration_runner.SUPPORTED_SCHEMAS if value < "0018"
        )
        old_reader.setattr(migration_runner, "SUPPORTED_SCHEMAS", prior_schemas)
        with pytest.raises(UnsupportedFormat, match="совместимую"):
            open_vault(target, PASSWORD)
    assert hashes(target) == target_before
    with closing(open_vault(source, PASSWORD)) as reopened:
        assert reopened.investment_positions.get(record.id) == record


@pytest.mark.parametrize(
    "statement",
    [
        "DROP TABLE investment_position_revisions",
        "ALTER TABLE investment_position_revisions_0018",
        "CREATE TRIGGER investment_position_payload_scope",
    ],
)
def test_candidate_ddl_rolls_back_rows_metadata_and_triggers(session_0017, monkeypatch, statement):
    session = session_0017
    account = session.catalog.add_account("Учебный брокерский счёт", AccountKind.INVESTMENT)
    saved = session.investment_positions.save(inputs(account))
    before = snapshot(session.engine)
    execute = op.execute

    def fail(sql, *args, **kwargs):
        result = execute(sql, *args, **kwargs)
        if isinstance(sql, str) and sql.startswith(statement):
            raise RuntimeError("Синтетический сбой миграции")
        return result

    with monkeypatch.context() as patch:
        patch.setattr(op, "execute", fail)
        with pytest.raises(RuntimeError, match="Синтетический сбой"):
            upgrade(session.engine)
    assert snapshot(session.engine) == before
    assert session.investment_positions.get(saved.id) == saved
    session.check_integrity()
