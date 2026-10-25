from uuid import uuid4

import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from personal_finance.domain.errors import ValidationError
from personal_finance.domain.models import AccountKind, CategoryKind


def test_command_is_idempotent_and_history_is_atomic(session) -> None:
    command = str(uuid4())
    first = session.catalog.add_account("Наличные", AccountKind.CASH, command_id=command)
    again = session.catalog.add_account("Наличные", AccountKind.CASH, command_id=command)
    assert first.id == again.id
    assert len(session.catalog.accounts()) == 1
    with pytest.raises(ValidationError):
        session.catalog.add_account("Другое", AccountKind.CASH, command_id=command)
    with session.engine.connect() as connection:
        assert (
            connection.execute(
                text("""SELECT count(*) FROM entity_revisions r
            JOIN change_log c ON c.revision_id=r.id
            WHERE r.id=:id AND r.sealed=1"""),
                {"id": first.revision_id},
            ).scalar_one()
            == 1
        )
    session.check_integrity()


def test_validation_cannot_leave_half_created_entity(session) -> None:
    with session.engine.connect() as connection:
        original = connection.execute(text("SELECT count(*) FROM entities")).scalar_one()
    with pytest.raises(ValidationError):
        session.catalog.add_account("Ошибка", AccountKind.DEBIT, str(uuid4()))
    parent = session.catalog.add_category("Доходы", CategoryKind.INCOME)
    with pytest.raises(ValidationError):
        session.catalog.add_category("Неверная ветка", CategoryKind.EXPENSE, parent.id)
    with session.engine.connect() as connection:
        assert (
            connection.execute(text("SELECT count(*) FROM entities")).scalar_one() == original + 1
        )
        assert (
            connection.execute(
                text("SELECT count(*) FROM entity_revisions WHERE sealed=0")
            ).scalar_one()
            == 0
        )


def test_completed_revisions_are_immutable(session) -> None:
    account = session.catalog.add_account("Наличные", AccountKind.CASH)
    for statement in [
        "UPDATE account_revisions SET name='Изменено' WHERE revision_id=:id",
        "DELETE FROM account_revisions WHERE revision_id=:id",
        "UPDATE entity_revisions SET is_deleted=1 WHERE id=:id",
        "DELETE FROM entity_revisions WHERE id=:id",
    ]:
        with pytest.raises(IntegrityError), session.engine.begin() as connection:
            connection.execute(text(statement), {"id": account.revision_id})
    assert session.catalog.accounts()[0].name == "Наличные"


def test_sql_text_in_name_is_data(session) -> None:
    name = "Счёт '; DROP TABLE books; --"
    session.catalog.add_account(name, AccountKind.CASH)
    assert session.catalog.accounts()[0].name == name
    session.check_integrity()


def test_integrity_detects_bad_revision_checksum(session) -> None:
    from personal_finance.domain.errors import VaultError

    # Внедряем ошибку миграции, временно обходя защитный триггер на тестовой базе.
    with session.engine.begin() as connection:
        connection.exec_driver_sql("DROP TRIGGER revision_no_update")
        connection.exec_driver_sql("UPDATE entity_revisions SET content_hash='invalid'")
    with pytest.raises(VaultError, match="хеш"):
        session.check_integrity()
