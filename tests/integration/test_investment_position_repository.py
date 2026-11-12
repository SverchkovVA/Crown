"""Синтетические проверки подготовленной, неактивной схемы 0017."""

from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from uuid import uuid4

import pytest
from alembic.operations import Operations
from alembic.runtime.migration import MigrationContext
from sqlalchemy import event
from sqlalchemy.exc import DatabaseError

from personal_finance.domain.errors import UnsupportedFormat, ValidationError
from personal_finance.domain.ledger import OperationDraft, OperationKind
from personal_finance.domain.models import AccountKind, Money
from personal_finance.infrastructure.persistence.investment_position_repository import (
    SqlInvestmentPositions,
)
from personal_finance.infrastructure.persistence.migration_runner import (
    initialize_schema,
    supported_schema,
)
from personal_finance.infrastructure.persistence.migrations.candidates import (
    v0017_investment_positions as candidate,
)
from tests.integration.test_generic_ocr_migration import staging_connection
from tests.integration.test_planning_scenario_migration import snapshot
from tests.unit.test_investment_positions import inputs
from tests.unit.test_investments import TODAY


@pytest.fixture
def storage(session):
    account = session.catalog.add_account("Синтетический инвестсчёт", AccountKind.INVESTMENT)
    session.ledger.add(OperationDraft(OperationKind.OPENING, TODAY, account.id, Money(10000)))
    with staging_connection(session.engine):
        candidate.upgrade()
    repo = SqlInvestmentPositions(
        session.engine, session.book_id, session.replica_id, session.mutex
    )
    return session, account, repo


def test_working_schema_remains_0016_and_candidate_requires_staging(session):
    account = session.catalog.add_account("Синтетический инвестсчёт", AccountKind.INVESTMENT)
    session.ledger.add(OperationDraft(OperationKind.OPENING, TODAY, account.id, Money(10000)))
    before = snapshot(session.engine)
    assert session.schema_revision == supported_schema(session.engine) == "0016"
    assert "investment_position_revisions" not in before[1]
    with pytest.raises(UnsupportedFormat):
        initialize_schema(session.engine, target="0017")
    with (
        pytest.raises(RuntimeError, match="промежуточной"),
        session.engine.begin() as connection,
        Operations.context(MigrationContext.configure(connection)),
    ):
        candidate.upgrade()
    assert snapshot(session.engine) == before
    with staging_connection(session.engine):
        candidate.upgrade()
    after = snapshot(session.engine)
    assert all(after[1][name] == rows for name, rows in before[1].items())
    new_objects = {row[1]: row for row in after[0]}
    for row in before[0]:
        if row[1] not in {"entities", "revision_payload_valid"}:
            assert new_objects[row[1]] == row
    assert after[1]["investment_position_revisions"] == []
    with session.engine.connect() as connection:
        assert connection.exec_driver_sql("PRAGMA foreign_keys").scalar_one() == 1
        assert connection.exec_driver_sql("PRAGMA foreign_key_check").fetchall() == []
        assert (
            connection.exec_driver_sql(
                "SELECT strict FROM pragma_table_list WHERE name='investment_position_revisions'"
            ).scalar_one()
            == 1
        )


def test_candidate_migration_rolls_back_after_creating_table(session, monkeypatch):
    session.catalog.add_account("Учебные наличные", AccountKind.CASH)
    before = snapshot(session.engine)
    original = candidate._create_position_table

    def abort():
        original()
        raise RuntimeError("injected after table")

    monkeypatch.setattr(candidate, "_create_position_table", abort)
    with pytest.raises(RuntimeError, match="injected"), staging_connection(session.engine):
        candidate.upgrade()
    assert snapshot(session.engine) == before


def test_save_update_archive_and_retry_preserve_history_and_cash_journal(storage):
    session, account, repo = storage
    original = inputs(account)
    before = (session.ledger.operations(), session.ledger.balances())
    command = str(uuid4())
    first = repo.save(original, command_id=command)
    changed = replace(original, security=replace(original.security, name="Обновлённое имя"))
    second = repo.save(changed, identifier=first.id, expected_revision_id=first.revision_id)
    assert repo.save(original, command_id=command) == first
    with pytest.raises(ValidationError, match="других данных"):
        repo.save(changed, command_id=command)
    with pytest.raises(ValidationError, match="изменилась"):
        repo.save(original, identifier=first.id, expected_revision_id=first.revision_id)
    with pytest.raises(ValidationError, match="таким кодом"):
        repo.save(original)
    archived = repo.save(
        changed, identifier=first.id, expected_revision_id=second.revision_id, is_deleted=True
    )
    assert repo.items() == [] and repo.items(include_archived=True) == [archived]
    restored = repo.save(changed, identifier=first.id, expected_revision_id=archived.revision_id)
    assert repo.history(first.id) == [first, second, archived, restored]
    assert repo.get(first.id) == restored and repo.items() == [restored]
    assert (session.ledger.operations(), session.ledger.balances()) == before
    repo.check_integrity()


def test_position_scope_and_identity_fail_closed(storage, monkeypatch):
    session, account, repo = storage
    cash = session.catalog.add_account("Учебные наличные", AccountKind.CASH)
    with pytest.raises(ValidationError, match="инвестиционный счёт"):
        repo.save(inputs(cash))
    first = repo.save(inputs(account))
    other = session.catalog.add_account("Второй инвестсчёт", AccountKind.INVESTMENT)
    with pytest.raises(ValidationError, match="сохраняются"):
        repo.save(inputs(other), identifier=first.id, expected_revision_id=first.revision_id)
    # Имитируем обход проверок прикладного слоя, чтобы проверить ограничение SQL.
    monkeypatch.setattr(
        repo, "_account", lambda *_args, **_kwargs: replace(cash, kind=AccountKind.INVESTMENT)
    )
    with pytest.raises(DatabaseError, match="invalid investment position scope"):
        repo.save(inputs(cash))


@pytest.mark.parametrize(
    "statement",
    [
        "UPDATE investment_position_revisions SET security_code='OTHER'",
        "DELETE FROM investment_position_revisions",
    ],
)
def test_raw_sql_cannot_replace_or_remove_payload(storage, statement):
    session, account, repo = storage
    saved = repo.save(inputs(account))
    with (
        pytest.raises(DatabaseError, match="immutable investment position payload"),
        session.engine.begin() as connection,
    ):
        connection.exec_driver_sql(statement)
    assert repo.get(saved.id) == saved


@pytest.mark.parametrize(
    "failed_step",
    [
        "UPDATE replicas SET sequence=sequence+1",
        "INSERT INTO investment_position_revisions",
        "UPDATE entities SET accepted_revision_id",
        "INSERT INTO change_log",
        "INSERT INTO command_receipts",
    ],
)
def test_interrupted_write_rolls_back_the_entire_group(storage, failed_step):
    session, account, repo = storage
    saved = repo.save(inputs(account))
    original = snapshot(session.engine)

    def abort(_conn, _cursor, statement, _parameters, _context, _many):
        if statement.strip().startswith(failed_step):
            raise RuntimeError("injected investment write")

    event.listen(session.engine, "before_cursor_execute", abort)
    try:
        with pytest.raises(RuntimeError, match="injected"):
            repo.save(saved.inputs, identifier=saved.id, expected_revision_id=saved.revision_id)
    finally:
        event.remove(session.engine, "before_cursor_execute", abort)
    assert snapshot(session.engine) == original
    assert repo.history(saved.id) == [saved]


def test_concurrent_changes_accept_only_one_expected_version(storage):
    _session, account, repo = storage
    saved = repo.save(inputs(account))

    def edit(name):
        changed = replace(saved.inputs, security=replace(saved.inputs.security, name=name))
        try:
            return repo.save(changed, identifier=saved.id, expected_revision_id=saved.revision_id)
        except ValidationError as error:
            assert "изменилась" in str(error)
            return None

    with ThreadPoolExecutor(2) as workers:
        versions = list(workers.map(edit, ("Первая правка", "Вторая правка")))
    (accepted,) = [value for value in versions if value is not None]
    assert repo.history(saved.id) == [saved, accepted]
    repo.check_integrity()


def test_sql_rejects_duplicate_position_even_when_application_check_is_skipped(
    storage, monkeypatch
):
    _session, account, repo = storage
    first = repo.save(inputs(account))
    monkeypatch.setattr(repo, "_duplicate", lambda *_args: False)
    with pytest.raises(DatabaseError, match="duplicate active investment position"):
        repo.save(first.inputs)
    assert repo.items() == [first]


def test_sql_rejects_sealing_position_without_typed_payload(storage):
    session, account, repo = storage
    original = snapshot(session.engine)

    def skip_payload(_conn, _cursor, statement, parameters, _context, _many):
        if statement.strip().startswith("INSERT INTO investment_position_revisions"):
            return "SELECT 1", ()
        return statement, parameters

    event.listen(session.engine, "before_cursor_execute", skip_payload, retval=True)
    try:
        with pytest.raises(DatabaseError, match="missing typed payload"):
            repo.save(inputs(account))
    finally:
        event.remove(session.engine, "before_cursor_execute", skip_payload)
    assert snapshot(session.engine) == original
