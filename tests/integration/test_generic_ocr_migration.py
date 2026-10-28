"""DDL 0012 проверяется только на временных зашифрованных книгах pytest."""

from contextlib import contextmanager

import pytest
from alembic import op
from alembic.operations import Operations
from alembic.runtime.migration import MigrationContext

from personal_finance.application import vault
from personal_finance.domain.models import AccountKind, CategoryKind
from personal_finance.infrastructure.persistence.migration_runner import initialize_schema
from personal_finance.infrastructure.persistence.migrations.versions import v0012_generic_ocr
from tests.conftest import PASSWORD
from tests.integration.test_ocr_repository import selection


@pytest.fixture
def old11(tmp_path, monkeypatch):
    with monkeypatch.context() as patch:
        patch.setattr(
            vault, "initialize_schema", lambda engine: initialize_schema(engine, target="0011")
        )
        session, _ = vault.create_vault(
            tmp_path / "synthetic11.pfvault", "Вымышленная книга 0011", PASSWORD
        )
    try:
        yield session
    finally:
        session.close()


def populate(session):
    first = session.catalog.add_account("Вымышленный первый счёт", AccountKind.CASH)
    second = session.catalog.add_account("Вымышленный второй счёт", AccountKind.CASH)
    categories = {
        kind: next(c.id for c in session.catalog.categories() if c.kind == kind)
        for kind in CategoryKind
    }
    value = selection((first, second, categories))
    session.ocr_importing.confirm_many((value,))
    return value


def snapshot(engine):
    with engine.connect() as connection:
        objects = connection.exec_driver_sql(
            "SELECT type,name,tbl_name,sql FROM sqlite_master "
            "WHERE type IN ('index','trigger') ORDER BY type,name"
        ).fetchall()
        rows = connection.exec_driver_sql("SELECT * FROM ocr_records ORDER BY id").fetchall()
        children = connection.exec_driver_sql(
            "SELECT * FROM ocr_batch_items ORDER BY batch_id,position"
        ).fetchall()
        ddl = connection.exec_driver_sql(
            "SELECT sql FROM sqlite_master WHERE name='ocr_records'"
        ).scalar_one()
        tables = connection.exec_driver_sql(
            "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
        ).fetchall()
    return objects, rows, children, ddl, tables


@contextmanager
def staging_connection(engine):
    with engine.connect() as connection:
        driver = connection.connection.driver_connection
        driver.execute("PRAGMA foreign_keys=OFF")
        try:
            with connection.begin():
                with Operations.context(MigrationContext.configure(connection)):
                    yield connection
                assert connection.exec_driver_sql("PRAGMA foreign_key_check").fetchall() == []
        finally:
            driver.execute("PRAGMA foreign_keys=ON")


def test_component_migration_preserves_all_old_objects_values_and_strict(old11):
    populate(old11)
    old11.check_integrity()
    before = snapshot(old11.engine)
    finances = old11.ledger.operations(), old11.ledger.balances()
    with staging_connection(old11.engine):
        v0012_generic_ocr.upgrade()
    after = snapshot(old11.engine)
    assert before[:3] == after[:3] and before[4] == after[4]
    expected = before[3][before[3].index("(") :].replace(
        "bank IN ('Т-Банк','ВТБ')", "bank IN ('Т-Банк','ВТБ','Любой банк')"
    )
    assert after[3][after[3].index("(") :] == expected
    with old11.engine.connect() as connection:
        assert (
            connection.exec_driver_sql(
                "SELECT strict FROM pragma_table_list WHERE name='ocr_records'"
            ).scalar_one()
            == 1
        )
        assert connection.exec_driver_sql("PRAGMA foreign_keys").scalar_one() == 1
        assert connection.exec_driver_sql("PRAGMA foreign_key_check").fetchall() == []
    assert (old11.ledger.operations(), old11.ledger.balances()) == finances
    old11.check_integrity()


@pytest.mark.parametrize(
    "prefix",
    [
        'CREATE TABLE "ocr_records_0012"',
        "INSERT INTO ocr_records_0012",
        'DROP TRIGGER "ocr_batch_book"',
        "DROP TABLE ocr_records",
        "ALTER TABLE ocr_records_0012",
        "CREATE INDEX ix_ocr_file",
        "CREATE TRIGGER ocr_source_file_context",
    ],
)
def test_component_fault_rolls_back_table_rows_and_every_guard(old11, monkeypatch, prefix):
    populate(old11)
    before = snapshot(old11.engine)
    execute = op.execute
    calls = []

    def fail(statement, *args, **kwargs):
        result = execute(statement, *args, **kwargs)
        if str(statement).startswith(prefix):
            calls.append(True)
            raise OSError("Вымышленный отказ DDL 0012")
        return result

    monkeypatch.setattr(op, "execute", fail)
    with pytest.raises(OSError), staging_connection(old11.engine):
        v0012_generic_ocr.upgrade()
    assert calls == [True]
    assert snapshot(old11.engine) == before
    with old11.engine.connect() as connection:
        assert connection.exec_driver_sql("PRAGMA foreign_keys").scalar_one() == 1
        assert (
            connection.exec_driver_sql("SELECT version_num FROM alembic_version").scalar_one()
            == "0011"
        )
    old11.check_integrity()


def test_populated_database_with_foreign_keys_on_is_refused_before_ddl(old11):
    populate(old11)
    before = snapshot(old11.engine)
    with (
        pytest.raises(RuntimeError, match="промежуточной копии"),
        old11.engine.begin() as connection,
        Operations.context(MigrationContext.configure(connection)),
    ):
        v0012_generic_ocr.upgrade()
    assert snapshot(old11.engine) == before
    old11.check_integrity()


def test_empty_new_schema_can_rebuild_with_foreign_keys_on(old11):
    before = snapshot(old11.engine)
    with old11.engine.begin() as connection:
        with Operations.context(MigrationContext.configure(connection)):
            v0012_generic_ocr.upgrade()
        assert connection.exec_driver_sql("PRAGMA foreign_key_check").fetchall() == []
    after = snapshot(old11.engine)
    assert before[:3] == after[:3] and before[4] == after[4]
