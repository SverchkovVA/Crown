"""Компонентное хранение сценариев: новые временные книги, без Vault-подключения."""

from dataclasses import FrozenInstanceError, replace
from uuid import uuid4

import pytest
from sqlalchemy import event
from sqlalchemy.exc import DatabaseError

from personal_finance.application import vault
from personal_finance.domain.errors import ValidationError, VaultError
from personal_finance.domain.models import Money
from personal_finance.infrastructure.persistence.deposit_scenarios_repository import (
    SqlDepositScenariosRepository,
)
from personal_finance.infrastructure.persistence.migrations.versions import v0013_deposit_scenarios
from tests.conftest import PASSWORD
from tests.integration.test_deposit_scenario_migration import snapshot
from tests.integration.test_generic_ocr_migration import staging_connection
from tests.unit.test_deposits import scenario


@pytest.fixture
def storage(tmp_path, monkeypatch):
    from personal_finance.infrastructure.persistence.migration_runner import initialize_schema

    with monkeypatch.context() as patch:
        patch.setattr(
            vault, "initialize_schema", lambda engine: initialize_schema(engine, target="0012")
        )
        session, _ = vault.create_vault(
            tmp_path / "synthetic-scenarios.pfvault", "Синтетические сценарии", PASSWORD
        )
    with staging_connection(session.engine):
        v0013_deposit_scenarios.upgrade()
    repository = SqlDepositScenariosRepository(
        session.engine, session.book_id, session.replica_id, session.mutex
    )
    try:
        yield session, repository
    finally:
        session.close()


def test_create_revise_archive_restore_preserve_frozen_history_and_ledger(storage):
    session, repo = storage
    money = session.ledger.operations(), session.ledger.balances()
    first = repo.save("Сценарий", scenario())
    revised = repo.save(
        "Новые условия",
        replace(scenario(), initial=Money(200002)),
        identifier=first.id,
        expected_revision_id=first.revision_id,
    )
    archived = repo.save(
        revised.name,
        revised.scenario,
        identifier=first.id,
        expected_revision_id=revised.revision_id,
        is_deleted=True,
    )
    assert repo.items() == [] and repo.items(include_archived=True) == [archived]
    active = repo.save(
        archived.name,
        archived.scenario,
        identifier=first.id,
        expected_revision_id=archived.revision_id,
    )
    assert repo.history(first.id) == [first, revised, archived, active]
    assert repo.get(first.id) == active and repo.items() == [active]
    assert first.scenario.initial.minor == 100001
    with pytest.raises(FrozenInstanceError):
        first.name = "Нельзя"
    assert (session.ledger.operations(), session.ledger.balances()) == money
    repo.check_integrity()
    session.check_integrity()


def test_command_retry_returns_original_revision_even_after_later_edit(storage):
    _, repo = storage
    command = str(uuid4())
    first = repo.save("Сценарий", scenario(), command_id=command)
    second = repo.save(
        "Изменённый", scenario(), identifier=first.id, expected_revision_id=first.revision_id
    )
    assert repo.save("Сценарий", scenario(), command_id=command) == first
    assert repo.get(first.id) == second and len(repo.history(first.id)) == 2
    with pytest.raises(ValidationError, match="других данных"):
        repo.save("Другие условия", scenario(), command_id=command)
    repo.check_integrity()


def test_stale_update_name_conflict_and_invalid_forecast_have_no_effect(storage):
    session, repo = storage
    first = repo.save("Сценарий", scenario())
    second = repo.save(
        "Обновлённый", scenario(), identifier=first.id, expected_revision_id=first.revision_id
    )
    before = snapshot(session.engine)
    with pytest.raises(ValidationError, match="изменён"):
        repo.save(
            "Затереть", scenario(), identifier=first.id, expected_revision_id=first.revision_id
        )
    with pytest.raises(ValidationError, match="названием"):
        repo.save("ОБНОВЛЁННЫЙ", scenario())
    broken = replace(
        scenario(), movements=(replace(scenario().movements[0], amount=Money(-1000000)),)
    )
    with pytest.raises(ValidationError, match="остаток"):
        repo.save("Недостаточно", broken)
    assert snapshot(session.engine) == before
    assert repo.items() == [second]


@pytest.mark.parametrize(
    "options",
    [
        {"command_id": ""},
        {"command_id": False},
        {"is_deleted": 1},
        {"identifier": str(uuid4())},
        {"expected_revision_id": str(uuid4())},
        {"is_deleted": True},
    ],
)
def test_invalid_command_rejected_without_rows(storage, options):
    session, repo = storage
    before = snapshot(session.engine)
    with pytest.raises(ValidationError):
        repo.save("Тест", scenario(), **options)
    assert snapshot(session.engine) == before


@pytest.mark.parametrize(
    "prefix",
    [
        "INSERT INTO entities(",
        "UPDATE replicas SET sequence",
        "INSERT INTO change_groups(",
        "INSERT INTO entity_revisions",
        "INSERT INTO deposit_scenario_revisions",
        "UPDATE entity_revisions SET sealed",
        "UPDATE entities SET accepted_revision_id",
        "INSERT INTO revision_heads",
        "INSERT INTO change_log",
        "INSERT INTO command_receipts",
    ],
)
def test_failure_after_each_atomic_write_rolls_back_every_row(storage, prefix):
    session, repo = storage
    before = snapshot(session.engine)
    calls = []

    def fail(connection, cursor, statement, parameters, context, many):
        if " ".join(statement.split()).startswith(prefix):
            calls.append(True)
            raise OSError("Вымышленный отказ записи сценария")

    event.listen(session.engine, "after_cursor_execute", fail)
    try:
        with pytest.raises(OSError):
            repo.save("Не публиковать", scenario())
    finally:
        event.remove(session.engine, "after_cursor_execute", fail)
    assert calls == [True]
    assert snapshot(session.engine) == before
    repo.check_integrity()
    session.check_integrity()


@pytest.mark.parametrize(
    "statement",
    [
        "UPDATE deposit_scenario_revisions SET name='Не менять' WHERE revision_id=?",
        "DELETE FROM deposit_scenario_revisions WHERE revision_id=?",
        "INSERT INTO deposit_scenario_revisions(revision_id,name,name_key,scenario_json) "
        "VALUES (?,'Тест','тест','{}')",
    ],
)
def test_sql_prohibits_sealed_payload_mutation(storage, statement):
    session, repo = storage
    first = repo.save("Сценарий", scenario())
    before = snapshot(session.engine)
    with pytest.raises(DatabaseError, match="sealed"), session.engine.begin() as connection:
        connection.exec_driver_sql(statement, (first.revision_id,))
    assert snapshot(session.engine) == before
    repo.check_integrity()


def test_unaccepted_entity_is_reported_not_silently_ignored(storage):
    session, repo = storage
    with session.engine.begin() as connection:
        connection.exec_driver_sql(
            "INSERT INTO entities(id,book_id,kind) VALUES (?,?,'deposit_scenario')",
            (str(uuid4()), session.book_id),
        )
    with pytest.raises(VaultError, match="непринятый"):
        repo.check_integrity()


def test_unknown_and_other_kind_ids_do_not_return_scenarios(storage):
    session, repo = storage
    tag = session.organization.add_tag("Чужой тип")
    for identifier in [tag.id, str(uuid4())]:
        with pytest.raises(ValidationError):
            repo.get(identifier)
        with pytest.raises(ValidationError):
            repo.history(identifier)
    assert repo.items() == []


def test_closed_session_cannot_write_candidate_storage(storage):
    session, repo = storage
    session.close()
    with pytest.raises(VaultError):
        repo.save("После закрытия", scenario())


def test_application_preserves_archived_state_and_command_retries(storage):
    from personal_finance.application.deposit_scenarios import DepositScenarios

    _, repo = storage
    app = DepositScenarios(repo)
    first = app.save("Первый", scenario())
    command = str(uuid4())
    archived = app.archive(first.id, first.revision_id, command_id=command)
    renamed = app.rename(first.id, archived.revision_id, "Архивный")
    edited = app.save(
        "Условия в архиве",
        replace(scenario(), initial=Money(200002)),
        identifier=first.id,
        expected_revision_id=renamed.revision_id,
    )
    assert edited.is_deleted and app.items() == []
    restored = app.archive(first.id, edited.revision_id, archived=False)
    assert app.get(first.id) == restored and app.items() == [restored]
    assert app.archive(first.id, first.revision_id, command_id=command) == archived
    assert app.history(first.id) == [first, archived, renamed, edited, restored]
    app.check_integrity()


def test_application_rejects_unknown_expected_version_and_incomplete_pair(storage):
    from personal_finance.application.deposit_scenarios import DepositScenarios

    session, repo = storage
    app = DepositScenarios(repo)
    first = app.save("Первый", scenario())
    before = snapshot(session.engine)
    with pytest.raises(ValidationError):
        app.save("Ошибка", scenario(), identifier=first.id)
    for method in [
        lambda: app.rename(first.id, str(uuid4()), "Ошибка"),
        lambda: app.archive(first.id, str(uuid4())),
    ]:
        with pytest.raises(ValidationError, match="Ожидаемая версия"):
            method()
    assert snapshot(session.engine) == before


def test_integrity_rejects_changed_payload_even_when_synthetic_guard_removed(storage):
    session, repo = storage
    first = repo.save("До подмены", scenario())
    with session.engine.begin() as connection:
        connection.exec_driver_sql("DROP TRIGGER deposit_scenario_no_update")
        connection.exec_driver_sql(
            "UPDATE deposit_scenario_revisions SET name=?,name_key=? WHERE revision_id=?",
            ("После подмены", "после подмены", first.revision_id),
        )
    with pytest.raises(VaultError, match="хешу"):
        repo.check_integrity()
