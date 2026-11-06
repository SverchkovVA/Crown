"""Компонент 0015 после перехода временной копии 0002–0014 к действующей 0014.

Это не сквозное восстановление книги 0015: совместимый reader и подключение
контейнера намеренно остаются отдельной обязательной работой перед активацией.
"""

from dataclasses import replace
from pathlib import Path
from shutil import copytree
from threading import RLock

import pytest

from personal_finance.application import vault
from personal_finance.domain.models import AccountKind
from personal_finance.infrastructure.persistence.database import make_engine
from personal_finance.infrastructure.persistence.migration_runner import (
    SCHEMA_HEAD,
    SUPPORTED_SCHEMAS,
    upgrade_restored_schema,
)
from personal_finance.infrastructure.persistence.migrations.candidates import v0015_planning
from personal_finance.infrastructure.persistence.planning_scenarios_repository import (
    SqlPlanningScenariosRepository,
)
from tests.integration.test_deposit_scenario_upgrade import create_at, hashes
from tests.integration.test_generic_ocr_migration import staging_connection
from tests.integration.test_planning_scenario_migration import snapshot
from tests.unit.test_planning_scenarios import goal


@pytest.mark.parametrize("version", tuple(f"{i:04d}" for i in range(2, 15)))
def test_candidate_preserves_rows_of_staging_copy_from_each_supported_schema(
    tmp_path, monkeypatch, version
):
    root = tmp_path / "source.pfvault"
    session, _ = create_at(monkeypatch, root, version)
    try:
        session.catalog.add_account("Синтетический счёт источника", AccountKind.CASH)
        book_id, replica_id = session.book_id, session.replica_id
        key = session.backup_key()
        session.check_integrity()
    finally:
        session.close()
    source_hash = hashes(root)
    target = tmp_path / "isolated-stage.pfvault"
    copytree(root, target)
    engine = make_engine(target / "data" / "ledger.sqlite3", key)
    try:
        upgrade_restored_schema(engine, version)
        before = snapshot(engine)
        with staging_connection(engine):
            v0015_planning.upgrade()
        after = snapshot(engine)
        assert all(after[1][name] == rows for name, rows in before[1].items())
        repo = SqlPlanningScenariosRepository(engine, book_id, replica_id, RLock())
        first = repo.save("Синтетическая цель", goal())
        second = repo.save(
            "Изменение цели",
            replace(goal(), deadline=None),
            identifier=first.id,
            expected_revision_id=first.revision_id,
        )
        assert repo.history(first.id) == [first, second]
        repo.check_integrity()
        with engine.connect() as connection:
            assert connection.exec_driver_sql("PRAGMA foreign_key_check").fetchall() == []
    finally:
        engine.dispose()
    assert hashes(root) == source_hash


def test_candidate_is_outside_active_alembic_versions():
    assert SCHEMA_HEAD == "0014" and "0015" not in SUPPORTED_SCHEMAS
    versions = Path(vault.__file__).parents[1] / "infrastructure/persistence/migrations/versions"
    assert not list(versions.glob("*0015*"))
