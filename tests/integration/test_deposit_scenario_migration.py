"""Изолированный кандидат 0013 на синтетических SQLCipher-книгах."""

from datetime import date

import pytest
from alembic import op
from sqlalchemy.exc import DatabaseError

from personal_finance.application import vault
from personal_finance.domain.errors import ValidationError
from personal_finance.domain.ledger import OperationDraft, OperationKind
from personal_finance.domain.models import CategoryKind, Money
from personal_finance.domain.periods import AccountingMonth
from personal_finance.infrastructure.persistence.migration_runner import (
    SCHEMA_HEAD,
    supported_schema,
)
from personal_finance.infrastructure.persistence.migrations.versions import (
    v0013_deposit_scenarios as candidate,
)
from tests.conftest import PASSWORD
from tests.integration.test_generic_ocr_migration import populate, staging_connection


@pytest.fixture
def session(tmp_path):
    value, _ = vault.create_vault(
        tmp_path / "synthetic12.pfvault", "Синтетическая книга 0012", PASSWORD
    )
    try:
        yield value
    finally:
        value.close()


def snapshot(engine):
    with engine.connect() as connection:
        objects = connection.exec_driver_sql(
            "SELECT type,name,tbl_name,sql FROM sqlite_master ORDER BY type,name"
        ).fetchall()
        tables = [
            row[1] for row in objects if row[0] == "table" and not row[1].startswith("sqlite_")
        ]
        rows = {
            name: connection.exec_driver_sql(
                'SELECT * FROM "' + name + '" ORDER BY rowid'
            ).fetchall()
            for name in tables
        }
    return objects, rows


def prepare(session):
    populate(session)
    tag = session.organization.add_tag("Вымышленный тег до 0013")
    session.periods.close(AccountingMonth(2026, 1))
    return tag


def test_exact_old_rows_guards_foreign_keys_and_history_preserved(session):
    tag = prepare(session)
    session.check_integrity()
    before = snapshot(session.engine)
    money = session.ledger.operations(), session.ledger.balances()
    with staging_connection(session.engine):
        candidate.upgrade()
    after = snapshot(session.engine)
    assert all(after[1][name] == values for name, values in before[1].items())
    previous = {row[1]: row for row in before[0]}
    current = {row[1]: row for row in after[0]}
    for name, row in previous.items():
        if name not in {"entities", "revision_payload_valid"}:
            assert current[name] == row
    assert "deposit_scenario_revisions" in current["revision_payload_valid"][3]
    assert "'deposit_scenario'" in current["entities"][3]
    assert after[1]["deposit_scenario_revisions"] == []
    with session.engine.connect() as connection:
        for table in ["entities", "deposit_scenario_revisions"]:
            assert (
                connection.exec_driver_sql(
                    "SELECT strict FROM pragma_table_list WHERE name=?", (table,)
                ).scalar_one()
                == 1
            )
        assert connection.exec_driver_sql("PRAGMA foreign_keys").scalar_one() == 1
        assert connection.exec_driver_sql("PRAGMA foreign_key_check").fetchall() == []
    with pytest.raises(DatabaseError, match="sealed"), session.engine.begin() as connection:
        connection.exec_driver_sql(
            "UPDATE tag_revisions SET name=? WHERE revision_id=?", ("Не менять", tag.revision_id)
        )
    account = session.catalog.accounts()[0]
    category = next(c for c in session.catalog.categories() if c.kind == CategoryKind.EXPENSE)
    with pytest.raises(ValidationError, match="закрыт"):
        session.ledger.add(
            OperationDraft(
                OperationKind.EXPENSE, date(2026, 1, 16), account.id, Money(1), category.id
            )
        )
    assert (session.ledger.operations(), session.ledger.balances()) == money
    session.check_integrity()


@pytest.mark.parametrize(
    "prefix",
    [
        'CREATE TABLE "entities_0013"',
        "INSERT INTO entities_0013",
        'DROP TRIGGER "',
        "DROP TABLE entities",
        "ALTER TABLE entities_0013",
        "CREATE TABLE deposit_scenario_revisions",
        "CREATE INDEX ix_deposit_scenario_name",
        "CREATE TRIGGER revision_payload_valid",
        "CREATE TRIGGER deposit_scenario_no_update",
        "CREATE TRIGGER deposit_scenario_count_valid",
    ],
)
def test_fault_at_every_boundary_rolls_back_all_tables_and_protections(
    session, monkeypatch, prefix
):
    prepare(session)
    before = snapshot(session.engine)
    original = op.execute
    calls = []

    def fail(statement, *args, **kwargs):
        result = original(statement, *args, **kwargs)
        if str(statement).startswith(prefix):
            calls.append(True)
            raise OSError("Вымышленный отказ 0013")
        return result

    monkeypatch.setattr(op, "execute", fail)
    with pytest.raises(OSError), staging_connection(session.engine):
        candidate.upgrade()
    assert calls == [True]
    assert snapshot(session.engine) == before
    assert supported_schema(session.engine) == "0012"
    with session.engine.connect() as connection:
        assert connection.exec_driver_sql("PRAGMA foreign_keys").scalar_one() == 1
    session.check_integrity()


def test_populated_fk_on_refused_before_any_change(session):
    prepare(session)
    before = snapshot(session.engine)
    from alembic.operations import Operations
    from alembic.runtime.migration import MigrationContext

    with (
        pytest.raises(RuntimeError, match="промежуточной"),
        session.engine.begin() as connection,
        Operations.context(MigrationContext.configure(connection)),
    ):
        candidate.upgrade()
    assert snapshot(session.engine) == before


def test_unknown_view_refused_before_ddl(session):
    with session.engine.begin() as connection:
        connection.exec_driver_sql("CREATE VIEW synthetic_entities AS SELECT id FROM entities")
    before = snapshot(session.engine)
    with pytest.raises(RuntimeError, match="представление"), staging_connection(session.engine):
        candidate.upgrade()
    assert snapshot(session.engine) == before


def test_component_downgrade_refused():
    with pytest.raises(RuntimeError, match="Понижение"):
        candidate.downgrade()


def test_default_head_unchanged_and_reopen_does_not_apply_candidate(tmp_path):
    assert SCHEMA_HEAD == "0012"
    folder = tmp_path / "no-auto.pfvault"
    session, _ = vault.create_vault(folder, "Вымышленная книга", PASSWORD)
    assert supported_schema(session.engine) == "0012"
    with session.engine.connect() as connection:
        assert not connection.exec_driver_sql(
            "SELECT 1 FROM sqlite_master WHERE name='deposit_scenario_revisions'"
        ).fetchall()
    session.close()
    reopened = vault.open_vault(folder, PASSWORD)
    try:
        assert supported_schema(reopened.engine) == "0012"
        assert not any(
            row[1] == "deposit_scenario_revisions" for row in snapshot(reopened.engine)[0]
        )
    finally:
        reopened.close()


def test_empty_new_schema_applies_with_fk_on(tmp_path):
    import os
    from contextlib import closing

    from alembic.operations import Operations
    from alembic.runtime.migration import MigrationContext

    from personal_finance.infrastructure.persistence.database import connect_database, make_engine
    from personal_finance.infrastructure.persistence.migration_runner import initialize_schema

    path = tmp_path / "blank.db"
    key = os.urandom(32)
    with closing(connect_database(path, key, create=True)):
        pass
    engine = make_engine(path, key)
    try:
        initialize_schema(engine, target="0012")
        with (
            engine.begin() as connection,
            Operations.context(MigrationContext.configure(connection)),
        ):
            assert connection.exec_driver_sql("PRAGMA foreign_keys").scalar_one() == 1
            assert connection.exec_driver_sql("SELECT count(*) FROM entities").scalar_one() == 0
            candidate.upgrade()
            assert connection.exec_driver_sql("PRAGMA foreign_key_check").fetchall() == []
            assert (
                connection.exec_driver_sql(
                    "SELECT count(*) FROM deposit_scenario_revisions"
                ).scalar_one()
                == 0
            )
    finally:
        engine.dispose()
