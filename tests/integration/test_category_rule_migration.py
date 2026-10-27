from datetime import date

import pytest
from alembic import command, op
from sqlalchemy import text
from sqlalchemy.exc import DatabaseError

from personal_finance.application import vault as vault_module
from personal_finance.domain.errors import ValidationError
from personal_finance.domain.ledger import OperationDraft, OperationKind
from personal_finance.domain.models import AccountKind, CategoryKind, Money
from personal_finance.domain.periods import AccountingMonth
from personal_finance.infrastructure.persistence.migration_runner import (
    _configuration,
    initialize_schema,
)
from tests.conftest import PASSWORD


@pytest.fixture
def session(tmp_path, monkeypatch):
    with monkeypatch.context() as patch:
        patch.setattr(
            vault_module, "initialize_schema", lambda e: initialize_schema(e, target="0008")
        )
        result, _ = vault_module.create_vault(tmp_path / "old8.pfvault", "Книга 0008", PASSWORD)
    try:
        yield result
    finally:
        result.close()


def triggers(connection):
    return dict(
        connection.exec_driver_sql(
            "SELECT name,sql FROM sqlite_master WHERE type='trigger'"
        ).fetchall()
    )


def upgrade_rules(engine):
    config = _configuration()
    with engine.connect() as connection:
        driver = connection.connection.driver_connection
        assert driver is not None
        driver.execute("PRAGMA foreign_keys=OFF")
        try:
            with connection.begin():
                config.attributes["connection"] = connection
                command.upgrade(config, "0009")
                assert not connection.exec_driver_sql("PRAGMA foreign_key_check").fetchall()
        finally:
            driver.execute("PRAGMA foreign_keys=ON")


def test_rule_migration_preserves_exact_existing_guards_and_financial_history(session):
    account = session.catalog.add_account("Счёт проверки миграции", AccountKind.CASH)
    category = next(c.id for c in session.catalog.categories() if c.kind == CategoryKind.EXPENSE)
    operation = session.ledger.add(
        OperationDraft(OperationKind.EXPENSE, date(2026, 1, 15), account.id, Money(100), category)
    )
    tag = session.organization.add_tag("Тег до миграции")
    session.periods.close(AccountingMonth(2026, 1))
    before = (
        session.ledger.operations(),
        session.ledger.balances(),
        session.ledger.history(operation.id),
    )
    with session.engine.connect() as connection:
        old_guards = triggers(connection)
    upgrade_rules(session.engine)
    with session.engine.connect() as connection:
        new_guards = triggers(connection)
        for name, sql in old_guards.items():
            if name != "revision_payload_valid":
                assert new_guards[name] == sql
        assert "category_rule_revisions" in new_guards["revision_payload_valid"]
        assert (
            connection.exec_driver_sql("SELECT version_num FROM alembic_version").scalar_one()
            == "0009"
        )
        assert (
            connection.exec_driver_sql("SELECT count(*) FROM category_rule_revisions").scalar_one()
            == 0
        )
        assert (
            connection.exec_driver_sql(
                "SELECT strict FROM pragma_table_list WHERE name='entities'"
            ).scalar_one()
            == 1
        )
    assert (
        session.ledger.operations(),
        session.ledger.balances(),
        session.ledger.history(operation.id),
    ) == before
    with pytest.raises(DatabaseError, match="sealed"), session.engine.begin() as connection:
        connection.execute(
            text("UPDATE tag_revisions SET name='Не менять' WHERE revision_id=:id"),
            {"id": tag.revision_id},
        )
    with pytest.raises(ValidationError, match="закрыт"):
        session.ledger.add(
            OperationDraft(OperationKind.EXPENSE, date(2026, 1, 16), account.id, Money(1), category)
        )
    session.check_integrity()


def test_failed_rule_migration_rolls_back_schema_and_all_old_triggers(session, monkeypatch):
    with session.engine.connect() as connection:
        original_guards = triggers(connection)
    create_table = op.create_table

    def fail(name, *args, **kwargs):
        result = create_table(name, *args, **kwargs)
        if name == "category_rule_revisions":
            raise OSError("Синтетический отказ миграции правил")
        return result

    monkeypatch.setattr(op, "create_table", fail)
    with pytest.raises(OSError, match="Синтетический отказ"):
        upgrade_rules(session.engine)
    with session.engine.connect() as connection:
        assert (
            connection.exec_driver_sql("SELECT version_num FROM alembic_version").scalar_one()
            == "0008"
        )
        assert triggers(connection) == original_guards
        assert not connection.exec_driver_sql(
            "SELECT 1 FROM sqlite_master WHERE name='category_rule_revisions'"
        ).fetchall()
        assert connection.exec_driver_sql("PRAGMA foreign_keys").scalar_one() == 1
    session.check_integrity()
