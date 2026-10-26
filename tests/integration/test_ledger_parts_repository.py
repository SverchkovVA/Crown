import hashlib
from dataclasses import replace
from datetime import date
from uuid import uuid4

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DatabaseError

from personal_finance.application import vault as vault_module
from personal_finance.domain.errors import ValidationError, VaultError
from personal_finance.domain.ledger import OperationDraft, OperationKind, OperationPart
from personal_finance.domain.models import AccountKind, CategoryKind, Money
from personal_finance.infrastructure.backup.container import (
    create_backup,
    restore_backup,
    verify_backup,
)
from personal_finance.infrastructure.persistence import migration_runner
from personal_finance.infrastructure.persistence.ledger_repository import SqlLedgerRepository
from tests.conftest import BACKUP_PASSWORD, PASSWORD

DAY = date(2026, 10, 27)


@pytest.fixture
def split_data(session):
    account = session.catalog.add_account("Счёт разделения", AccountKind.CASH)
    income = [c.id for c in session.catalog.categories() if c.kind == CategoryKind.INCOME]
    expense = [c.id for c in session.catalog.categories() if c.kind == CategoryKind.EXPENSE]
    return account.id, income, expense


def draft(account, categories, kind=OperationKind.EXPENSE):
    return OperationDraft(
        kind,
        DAY,
        account,
        Money(1000),
        parts=(OperationPart(categories[0], Money(700)), OperationPart(categories[1], Money(300))),
    )


@pytest.mark.parametrize("kind", [OperationKind.INCOME, OperationKind.EXPENSE])
def test_split_round_trip_idempotency_and_single_balance_effect(session, split_data, kind):
    account, income, expense = split_data
    ledger = session.ledger
    assert ledger is not None and ledger.supports_splits
    command = str(uuid4())
    value = draft(account, income if kind == OperationKind.INCOME else expense, kind)
    first = ledger.add(value, command_id=command)
    assert ledger.add(value, command_id=command) == first
    assert ledger.operations() == [first]
    assert ledger.balances()[0].amount.minor == (1000 if kind == OperationKind.INCOME else -1000)
    with session.engine.connect() as c:
        assert c.execute(text("SELECT count(*) FROM operation_parts")).scalar_one() == 2
        assert c.execute(text("SELECT count(*) FROM postings")).scalar_one() == 3
    with pytest.raises(ValidationError, match="других данных"):
        ledger.add(replace(value, parts=tuple(reversed(value.parts))), command_id=command)
    session.check_integrity()


def test_edit_split_to_plain_and_delete_preserve_all_parts(session, split_data, tmp_path):
    account, _, expense = split_data
    ledger = session.ledger
    first = ledger.add(draft(account, expense))
    second = ledger.replace(
        first.id,
        first.revision_id,
        replace(first.draft, parts=(), category_id=expense[0], amount=Money(1500)),
    )
    third = ledger.delete(second.id, second.revision_id)
    assert ledger.history(first.id) == [first, second, third]
    assert ledger.balances()[0].amount.minor == 0
    backup = create_backup(session, tmp_path / "parts.pfbackup", BACKUP_PASSWORD)
    restored, _ = restore_backup(backup, tmp_path / "restored.pfvault", BACKUP_PASSWORD, PASSWORD)
    try:
        assert restored.replica_id != session.replica_id
        assert restored.ledger.history(first.id) == [first, second, third]
        assert restored.ledger.balances()[0].amount.minor == 0
        restored.check_integrity()
    finally:
        restored.close()


def test_wrong_category_in_part_is_rejected_without_partial_rows(session, split_data):
    account, income, expense = split_data
    value = draft(account, [expense[0], income[0]])
    with pytest.raises(ValidationError):
        session.ledger.add(value)
    assert session.ledger.operations() == []
    with session.engine.connect() as c:
        assert c.execute(text("SELECT count(*) FROM operation_parts")).scalar_one() == 0
        assert c.execute(text("SELECT count(*) FROM postings")).scalar_one() == 0


def test_failure_after_parts_and_postings_rolls_back_entire_command(
    session, split_data, monkeypatch
):
    account, _, expense = split_data
    tables = (
        "entities",
        "entity_revisions",
        "change_groups",
        "change_log",
        "command_receipts",
        "ledger_accounts",
        "operation_revisions",
        "operation_parts",
        "postings",
    )

    def snapshot():
        with session.engine.connect() as c:
            return (
                [c.execute(text(f"SELECT count(*) FROM {table}")).scalar_one() for table in tables],
                c.execute(
                    text("SELECT sequence FROM replicas WHERE id=:id"), {"id": session.replica_id}
                ).scalar_one(),
            )

    before = snapshot()
    original = SqlLedgerRepository._insert_postings

    def fail(self, connection, revision_id, value):
        assert (
            connection.execute(
                text("SELECT count(*) FROM operation_parts WHERE revision_id=:id"),
                {"id": revision_id},
            ).scalar_one()
            == 2
        )
        original(self, connection, revision_id, value)
        raise OSError("Синтетический отказ после частей и проводок")

    monkeypatch.setattr(SqlLedgerRepository, "_insert_postings", fail)
    with pytest.raises(OSError):
        session.ledger.add(draft(account, expense))
    assert snapshot() == before
    session.check_integrity()


@pytest.mark.parametrize(
    "statement",
    [
        "UPDATE operation_parts SET amount_minor=1 WHERE revision_id=:id",
        "DELETE FROM operation_parts WHERE revision_id=:id",
        "INSERT INTO operation_parts(revision_id,position,category_id,amount_minor) "
        "VALUES (:id,2,:category,1)",
    ],
)
def test_sealed_parts_cannot_be_changed(session, split_data, statement):
    account, _, expense = split_data
    first = session.ledger.add(draft(account, expense))
    with pytest.raises(DatabaseError), session.engine.begin() as c:
        c.execute(text(statement), {"id": first.revision_id, "category": expense[2]})
    session.check_integrity()


def test_integrity_detects_changed_distribution_even_with_unchanged_total(session, split_data):
    account, _, expense = split_data
    first = session.ledger.add(draft(account, expense))
    with session.engine.begin() as c:
        c.exec_driver_sql("DROP TRIGGER operation_parts_no_update")
        c.execute(
            text(
                "UPDATE operation_parts SET amount_minor=CASE position WHEN 0 THEN 600 "
                "ELSE 400 END WHERE revision_id=:id"
            ),
            {"id": first.revision_id},
        )
    with pytest.raises(VaultError, match="хешу"):
        session.check_integrity()


def test_published_schema3_keeps_hashes_and_receipts_when_only_copy_is_upgraded(
    tmp_path, monkeypatch
):
    with monkeypatch.context() as patch:
        patch.setattr(
            vault_module,
            "initialize_schema",
            lambda engine: migration_runner.initialize_schema(engine, target="0003"),
        )
        source, _ = vault_module.create_vault(tmp_path / "old3.pfvault", "Старая схема 3", PASSWORD)
    restored = None
    try:
        account = source.catalog.add_account("Счёт прежней схемы", AccountKind.CASH)
        expense = [c.id for c in source.catalog.categories() if c.kind == CategoryKind.EXPENSE]
        command = str(uuid4())
        simple = OperationDraft(OperationKind.EXPENSE, DAY, account.id, Money(1000), expense[0])
        operation = source.ledger.add(simple, command_id=command)
        source.close()
        files = [source.root / "data" / name for name in ("ledger.sqlite3", "key.json")]
        before = [hashlib.sha256(p.read_bytes()).hexdigest() for p in files]
        source = vault_module.open_vault(source.root, PASSWORD)
        assert source.schema_revision == "0003"
        assert source.ledger.supports_splits is False
        assert source.ledger.operations() == [operation]
        with pytest.raises(ValidationError, match="в новую папку"):
            source.ledger.add(draft(account.id, expense))
        backup = create_backup(source, tmp_path / "old3.pfbackup", BACKUP_PASSWORD)
        assert verify_backup(backup, BACKUP_PASSWORD)["schema"] == "0003"
        restored, _ = restore_backup(backup, tmp_path / "new4.pfvault", BACKUP_PASSWORD, PASSWORD)
        assert restored.schema_revision == "0005" and restored.ledger.supports_splits
        assert restored.ledger.operations() == [operation]
        assert restored.ledger.add(simple, command_id=command) == operation
        replacement = restored.ledger.replace(
            operation.id, operation.revision_id, draft(account.id, expense)
        )
        assert restored.ledger.history(operation.id) == [operation, replacement]
        assert restored.ledger.balances()[0].amount.minor == -1000
        restored.check_integrity()
        assert [hashlib.sha256(p.read_bytes()).hexdigest() for p in files] == before
        source.check_integrity()
    finally:
        source.close()
        if restored is not None:
            restored.close()


@pytest.mark.parametrize(
    "mutation",
    [
        "UPDATE operation_parts SET amount_minor=amount_minor+1 "
        "WHERE revision_id=:id AND position=0",
        "UPDATE operation_parts SET position=2 WHERE revision_id=:id AND position=0",
        "DELETE FROM operation_parts WHERE revision_id=:id AND position=0",
    ],
)
def test_database_refuses_to_seal_incomplete_or_unbalanced_parts(
    session,
    split_data,
    monkeypatch,
    mutation,
):
    account, _, expense = split_data
    original = SqlLedgerRepository._insert_postings

    def corrupt(self, connection, revision_id, value):
        original(self, connection, revision_id, value)
        connection.execute(text(mutation), {"id": revision_id})

    monkeypatch.setattr(SqlLedgerRepository, "_insert_postings", corrupt)
    with pytest.raises(DatabaseError):
        session.ledger.add(draft(account, expense))
    assert session.ledger.operations() == []
    with session.engine.connect() as c:
        assert c.execute(text("SELECT count(*) FROM operation_parts")).scalar_one() == 0
        assert c.execute(text("SELECT count(*) FROM postings")).scalar_one() == 0
    session.check_integrity()


def test_integrity_detects_invalid_part_positions_without_changed_amounts(session, split_data):
    account, _, expense = split_data
    operation = session.ledger.add(draft(account, expense))
    with session.engine.begin() as c:
        c.exec_driver_sql("DROP TRIGGER operation_parts_no_update")
        c.execute(
            text("UPDATE operation_parts SET position=position+10 WHERE revision_id=:id"),
            {"id": operation.revision_id},
        )
    with pytest.raises(VaultError, match="последовательность частей"):
        session.check_integrity()


def test_failed_schema3_upgrade_never_publishes_target_or_changes_source(tmp_path, monkeypatch):
    with monkeypatch.context() as patch:
        patch.setattr(
            vault_module,
            "initialize_schema",
            lambda engine: migration_runner.initialize_schema(engine, target="0003"),
        )
        source, _ = vault_module.create_vault(tmp_path / "old.pfvault", "Старая схема", PASSWORD)
    try:
        source.catalog.add_account("Проверочный счёт", AccountKind.CASH)
        files = [source.root / "data" / name for name in ("ledger.sqlite3", "key.json")]
        before = [hashlib.sha256(p.read_bytes()).hexdigest() for p in files]
        backup = create_backup(source, tmp_path / "old.pfbackup", BACKUP_PASSWORD)
        upgrade = migration_runner.command.upgrade

        def fail(*args, **kwargs):
            upgrade(*args, **kwargs)
            raise OSError("Синтетический сбой после установки 0004")

        target = tmp_path / "failed.pfvault"
        with monkeypatch.context() as patch:
            patch.setattr(migration_runner.command, "upgrade", fail)
            with pytest.raises(OSError):
                restore_backup(backup, target, BACKUP_PASSWORD, PASSWORD)
        assert not target.exists()
        assert [hashlib.sha256(p.read_bytes()).hexdigest() for p in files] == before
        assert verify_backup(backup, BACKUP_PASSWORD)["schema"] == "0003"
        source.check_integrity()
    finally:
        source.close()


@pytest.mark.parametrize("count", [3, 100])
def test_split_supports_more_than_three_postings_and_maximum_parts(session, split_data, count):
    account, _, _ = split_data
    categories = [
        session.catalog.add_category(f"Часть {index}", CategoryKind.EXPENSE).id
        for index in range(count)
    ]
    value = OperationDraft(
        OperationKind.EXPENSE,
        DAY,
        account,
        Money(count),
        parts=tuple(OperationPart(category, Money(1)) for category in categories),
    )
    operation = session.ledger.add(value)
    assert session.ledger.operations() == [operation]
    assert session.ledger.balances()[0].amount.minor == -count
    with session.engine.connect() as c:
        assert c.execute(text("SELECT count(*) FROM postings")).scalar_one() == count + 1
    session.check_integrity()
