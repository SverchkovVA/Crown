"""Атомарность изменения и отказ устаревшего reader без изменения книги."""

import hashlib

import pytest
from sqlalchemy import event, text

from personal_finance.application import vault
from personal_finance.domain.errors import VaultError
from personal_finance.infrastructure.persistence import migration_runner
from tests.conftest import PASSWORD
from tests.integration.test_planning_scenario_migration import snapshot
from tests.integration.test_planning_scenario_repository import storage as storage
from tests.unit.test_planning_scenarios import goal


@pytest.mark.parametrize(
    "prefix",
    [
        "UPDATE replicas SET sequence",
        "INSERT INTO change_groups(",
        "INSERT INTO entity_revisions",
        "INSERT INTO planning_scenario_revisions",
        "INSERT INTO revision_parents",
        "UPDATE entity_revisions SET sealed",
        "UPDATE entities SET accepted_revision_id",
        "DELETE FROM revision_heads",
        "INSERT INTO revision_heads",
        "INSERT INTO change_log",
        "INSERT INTO command_receipts",
    ],
)
def test_failed_update_rolls_back_previous_head_and_version(storage, prefix):
    session, repo = storage
    first = repo.save("До изменения", goal())
    before = snapshot(session.engine)
    calls = []

    def fail(connection, cursor, statement, parameters, context, many):
        if " ".join(statement.split()).startswith(prefix):
            calls.append(True)
            raise OSError("Вымышленный отказ изменения условий")

    event.listen(session.engine, "after_cursor_execute", fail)
    try:
        with pytest.raises(OSError):
            repo.save(
                "После изменения",
                goal(),
                identifier=first.id,
                expected_revision_id=first.revision_id,
            )
    finally:
        event.remove(session.engine, "after_cursor_execute", fail)
    assert calls == [True]
    assert snapshot(session.engine) == before
    assert repo.get(first.id) == first and repo.history(first.id) == [first]
    repo.check_integrity()
    session.check_integrity()


def test_schema_0014_reader_refuses_0015_and_preserves_database_and_key(storage, monkeypatch):
    session, repo = storage
    repo.save("Синтетическая цель", goal())
    with session.engine.begin() as connection:
        connection.execute(text("UPDATE alembic_version SET version_num='0015'"))
    root = session.root
    database = session.database_path
    session.close()
    before = {
        path: hashlib.sha256(path.read_bytes()).hexdigest()
        for path in (database, root / "data" / "key.json")
    }
    monkeypatch.setattr(
        migration_runner,
        "SUPPORTED_SCHEMAS",
        tuple(v for v in migration_runner.SUPPORTED_SCHEMAS if v != "0015"),
    )
    with pytest.raises(VaultError):
        vault.open_vault(root, PASSWORD)
    assert {path: hashlib.sha256(path.read_bytes()).hexdigest() for path in before} == before
