"""Кандидат 0016 на новых и восстановленных синтетических книгах."""

import hashlib
import json
from contextlib import closing
from datetime import date
from uuid import uuid4

import pytest
from alembic import op
from sqlalchemy import event
from sqlalchemy.exc import DatabaseError

from personal_finance.application import vault
from personal_finance.domain.errors import InvalidCredentials, ValidationError, VaultError
from personal_finance.domain.ledger import OperationDraft, OperationKind
from personal_finance.domain.models import AccountKind, Money
from personal_finance.domain.planning_scenarios import PlanningKind
from personal_finance.infrastructure.backup import container
from personal_finance.infrastructure.persistence import migration_runner as migrations
from tests.conftest import BACKUP_PASSWORD, PASSWORD
from tests.integration.test_deposit_scenario_upgrade import create_at
from tests.integration.test_planning_scenario_migration import snapshot
from tests.integration.test_planning_scenario_upgrade import budget, hashes, state
from tests.unit.test_payment_calendar_codec import calendar
from tests.unit.test_planning_scenarios import goal

NEW_PASSWORD = "Новая вымышленная парольная фраза проверки календарей"


def restore16(monkeypatch, archive, target):
    with monkeypatch.context() as patch:
        patch.setattr(migrations, "SCHEMA_HEAD", "0016")
        return container.restore_backup(archive, target, BACKUP_PASSWORD, NEW_PASSWORD)


def test_calendar_saves_versions_and_survives_explicit_backup_restore(tmp_path, monkeypatch):
    source = tmp_path / "calendar.pfvault"
    session, old_pf = create_at(monkeypatch, source, "0016")
    try:
        account = session.catalog.add_account("Проверочный счёт", AccountKind.CASH)
        session.ledger.add(
            OperationDraft(OperationKind.OPENING, date(2026, 1, 1), account.id, Money(1000))
        )
        expected = state(session)
        service = session.planning_scenarios
        command = str(uuid4())
        first = service.save("Ежемесячные расходы", calendar(), command_id=command)
        assert service.save(first.name, calendar(), command_id=command) == first
        renamed = service.rename(first.id, first.revision_id, "Платежи на весну")
        archived = service.archive(renamed.id, renamed.revision_id)
        assert service.items(kind=PlanningKind.PAYMENT_CALENDAR) == []
        assert service.items(kind=PlanningKind.PAYMENT_CALENDAR, include_archived=True) == [
            archived
        ]
        assert service.history(first.id) == [first, renamed, archived]
        with pytest.raises(ValidationError, match="Вид"):
            service.save(
                "Подмена", goal(), identifier=first.id, expected_revision_id=archived.revision_id
            )
        assert state(session) == expected
        session.check_integrity()
        replica = session.replica_id
        archive = container.create_backup(session, tmp_path / "calendar.pfbackup", BACKUP_PASSWORD)
        assert container.verify_backup(archive, BACKUP_PASSWORD)["schema"] == "0016"
    finally:
        session.close()
    before = hashes(source)
    target = tmp_path / "restored.pfvault"
    restored, new_pf = restore16(monkeypatch, archive, target)
    try:
        assert restored.schema_revision == "0016" and restored.replica_id != replica
        assert new_pf != old_pf
        assert restored.planning_scenarios.history(first.id) == [first, renamed, archived]
        assert restored.planning_scenarios.save(first.name, calendar(), command_id=command) == first
        assert state(restored) == expected
        restored.check_integrity()
    finally:
        restored.close()
    assert hashes(source) == before
    with pytest.raises(InvalidCredentials):
        vault.open_vault(target, PASSWORD)
    for secret, recovery in ((NEW_PASSWORD, False), (new_pf, True)):
        with closing(vault.open_vault(target, secret, recovery=recovery)) as opened:
            assert opened.planning_scenarios.history(first.id) == [first, renamed, archived]


@pytest.mark.parametrize("version", tuple(f"{n:04d}" for n in range(2, 16)))
def test_each_previous_schema_restores_to_explicit16_and_keeps_source(
    tmp_path, monkeypatch, version
):
    source = tmp_path / "original.pfvault"
    session, _ = create_at(monkeypatch, source, version)
    try:
        account = session.catalog.add_account("Синтетический счёт", AccountKind.CASH)
        if session.ledger is not None:
            session.ledger.add(
                OperationDraft(OperationKind.OPENING, date(2026, 1, 1), account.id, Money(2000))
            )
        expected = state(session)
        plan = None
        if session.planning_scenarios is not None:
            plan = session.planning_scenarios.save("Проверочный бюджет", budget())
            session.planning_scenarios.save("Проверочная цель", goal())
            with pytest.raises(ValidationError, match="0016"):
                session.planning_scenarios.save("Ещё закрытый календарь", calendar())
        rows_before = snapshot(session.engine) if version == "0015" else None
        archive = container.create_backup(session, tmp_path / "original.pfbackup", BACKUP_PASSWORD)
    finally:
        session.close()
    before = hashes(source), hashlib.sha256(archive.read_bytes()).hexdigest()
    restored, _ = restore16(monkeypatch, archive, tmp_path / "new.pfvault")
    try:
        assert restored.schema_revision == "0016"
        actual = state(restored)
        assert all(actual[key] == value for key, value in expected.items())
        if rows_before is not None:
            objects, rows = snapshot(restored.engine)
            definitions = {row[1]: row for row in objects}
            for definition in rows_before[0]:
                if definition[1] != "planning_scenario_revisions":
                    assert definitions[definition[1]] == definition
            assert (
                rows["planning_scenario_revisions"] == rows_before[1]["planning_scenario_revisions"]
            )
            assert restored.planning_scenarios.get(plan.id) == plan
        saved = restored.planning_scenarios.save("Проверочный календарь", calendar())
        assert restored.planning_scenarios.get(saved.id) == saved
        restored.check_integrity()
    finally:
        restored.close()
    assert before == (hashes(source), hashlib.sha256(archive.read_bytes()).hexdigest())
    with closing(vault.open_vault(source, PASSWORD)) as original:
        assert original.schema_revision == version


@pytest.mark.parametrize(
    "prefix",
    (
        'CREATE TABLE "planning_scenario_revisions_0016"',
        "INSERT INTO planning_scenario_revisions_0016",
        "DROP TABLE planning_scenario_revisions",
        "ALTER TABLE planning_scenario_revisions_0016",
        "CREATE TRIGGER planning_calendar_schedule_valid",
    ),
)
def test_migration_failure_never_publishes_a_book(tmp_path, monkeypatch, prefix):
    source = tmp_path / "original.pfvault"
    session, _ = create_at(monkeypatch, source, "0015")
    try:
        session.planning_scenarios.save("Сохранённая цель", goal())
        archive = container.create_backup(session, tmp_path / "original.pfbackup", BACKUP_PASSWORD)
    finally:
        session.close()
    before = hashes(source), hashlib.sha256(archive.read_bytes()).hexdigest()
    execute = op.execute

    def fail(sql, *args, **kwargs):
        value = execute(sql, *args, **kwargs)
        if isinstance(sql, str) and sql.startswith(prefix):
            raise OSError("Вымышленный отказ этапа переноса")
        return value

    target = tmp_path / "failed.pfvault"
    with monkeypatch.context() as patch:
        patch.setattr(op, "execute", fail)
        with pytest.raises(OSError, match="Вымышленный отказ"):
            restore16(patch, archive, target)
    assert not target.exists()
    assert not list(tmp_path.glob(".pf-restore-*"))
    assert before == (hashes(source), hashlib.sha256(archive.read_bytes()).hexdigest())


def test_sql_guards_block_invalid_payload_and_old_reader_leaves_book_intact(tmp_path, monkeypatch):
    source = tmp_path / "calendar.pfvault"
    session, _ = create_at(monkeypatch, source, "0016")

    def corrupt(_connection, _cursor, statement, parameters, _context, _many):
        if statement.startswith("INSERT INTO planning_scenario_revisions"):
            payload = json.loads(parameters[-1])
            payload["schedules"][0]["amount_minor"] = 0
            parameters = (*parameters[:-1], json.dumps(payload))
        return statement, parameters

    try:
        before = snapshot(session.engine)
        event.listen(session.engine, "before_cursor_execute", corrupt, retval=True)
        try:
            with pytest.raises(DatabaseError, match="invalid payment schedule"):
                session.planning_scenarios.save("Проверочный календарь", calendar())
        finally:
            event.remove(session.engine, "before_cursor_execute", corrupt)
        assert snapshot(session.engine) == before
        session.planning_scenarios.save("Проверочный календарь", calendar())
    finally:
        session.close()
    before = hashes(source)
    monkeypatch.setattr(migrations, "SUPPORTED_SCHEMAS", migrations.SUPPORTED_SCHEMAS[:-1])
    with pytest.raises(VaultError):
        vault.open_vault(source, PASSWORD)
    assert hashes(source) == before
