import hashlib
from concurrent.futures import ThreadPoolExecutor
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
from personal_finance.domain.periods import AccountingMonth
from personal_finance.infrastructure.backup.container import (
    create_backup,
    restore_backup,
    verify_backup,
)
from personal_finance.infrastructure.persistence import ledger_repository, migration_runner
from personal_finance.infrastructure.persistence.period_repository import SqlPeriodRepository
from tests.conftest import BACKUP_PASSWORD, PASSWORD

MONTH = AccountingMonth(2026, 1)
DAY = date(2026, 1, 15)


@pytest.fixture
def financial(session):
    account = session.catalog.add_account("Проверочные наличные", AccountKind.CASH)
    other = session.catalog.add_account("Проверочный резерв", AccountKind.CASH)
    categories = [c.id for c in session.catalog.categories() if c.kind == CategoryKind.EXPENSE]
    expense = session.ledger.add(
        OperationDraft(
            OperationKind.EXPENSE,
            DAY,
            account.id,
            Money(10000),
            parts=(
                OperationPart(categories[0], Money(7000)),
                OperationPart(categories[1], Money(3000)),
            ),
        )
    )
    return expense, other, categories


def snapshot(session):
    with session.engine.connect() as c:
        return [
            c.execute(text("SELECT count(*) FROM " + name)).scalar_one()
            for name in (
                "entities",
                "entity_revisions",
                "change_groups",
                "change_log",
                "command_receipts",
                "postings",
                "operation_parts",
                "period_revisions",
            )
        ] + list(c.execute(text("SELECT sequence FROM replicas ORDER BY id")).scalars())


def test_close_reopen_and_close_again_preserve_history_and_balances(session, financial):
    expense, _, _ = financial
    before = session.ledger.operations(), session.ledger.balances()
    closed = session.periods.close(MONTH, reason="Проверено")
    opened = session.periods.reopen(MONTH, closed.revision_id, reason="Исправление")
    again = session.periods.close(MONTH, expected_revision_id=opened.revision_id)
    assert closed.id == opened.id == again.id
    assert session.periods.history(MONTH) == [closed, opened, again]
    assert session.periods.states() == [again]
    assert (session.ledger.operations(), session.ledger.balances()) == before
    session.check_integrity()


@pytest.mark.parametrize("kind", list(OperationKind))
def test_closed_month_refuses_every_kind_without_partial_writes(session, financial, kind):
    expense, other, categories = financial
    session.periods.close(MONTH)
    income = next(c.id for c in session.catalog.categories() if c.kind == CategoryKind.INCOME)
    value = OperationDraft(
        kind,
        DAY,
        expense.draft.account_id,
        Money(100),
        category_id=income
        if kind == OperationKind.INCOME
        else categories[0]
        if kind in (OperationKind.EXPENSE, OperationKind.REFUND)
        else None,
        destination_account_id=other.id if kind == OperationKind.TRANSFER else None,
        refund_of=expense.id if kind == OperationKind.REFUND else None,
        refund_of_revision_id=expense.revision_id if kind == OperationKind.REFUND else None,
    )
    before = snapshot(session)
    with pytest.raises(ValidationError, match="Период 01.2026 закрыт"):
        session.ledger.add(value)
    assert snapshot(session) == before
    session.check_integrity()


@pytest.mark.parametrize("action", ["edit", "move_out", "delete"])
def test_old_date_protects_closed_operation(session, financial, action):
    expense, _, _ = financial
    session.periods.close(MONTH)
    before = snapshot(session)
    with pytest.raises(ValidationError, match="Период"):
        if action == "delete":
            session.ledger.delete(expense.id, expense.revision_id)
        else:
            session.ledger.replace(
                expense.id,
                expense.revision_id,
                replace(
                    expense.draft,
                    note="Правка",
                    occurred_on=date(2026, 2, 1) if action == "move_out" else DAY,
                ),
            )
    assert snapshot(session) == before


def test_new_date_cannot_move_an_open_operation_into_closed_month(session, financial):
    expense, _, _ = financial
    later = session.ledger.add(replace(expense.draft, occurred_on=date(2026, 2, 1)))
    session.periods.close(MONTH)
    with pytest.raises(ValidationError, match="Период"):
        session.ledger.replace(later.id, later.revision_id, expense.draft)
    session.check_integrity()


def test_later_refund_of_closed_purchase_is_allowed(session, financial):
    expense, _, categories = financial
    session.periods.close(MONTH)
    session.ledger.add(
        OperationDraft(
            OperationKind.REFUND,
            date(2026, 2, 1),
            expense.draft.account_id,
            Money(1000),
            categories[0],
            refund_of=expense.id,
            refund_of_revision_id=expense.revision_id,
        )
    )
    assert {b.account_id: b.amount for b in session.ledger.balances(DAY)}[
        expense.draft.account_id
    ] == Money(-10000)
    assert {b.account_id: b.amount for b in session.ledger.balances(date(2026, 2, 1))}[
        expense.draft.account_id
    ] == Money(-9000)
    session.check_integrity()


def test_reopening_enables_changes_and_state_survives_reopen_vault(session, financial):
    expense, _, _ = financial
    closed = session.periods.close(MONTH)
    session.periods.reopen(MONTH, closed.revision_id, reason="Исправить примечание")
    session.ledger.replace(
        expense.id, expense.revision_id, replace(expense.draft, note="Исправлено")
    )
    root = session.root
    expected = session.periods.history(MONTH)
    session.close()
    reopened = vault_module.open_vault(root, PASSWORD)
    try:
        assert reopened.periods.history(MONTH) == expected
        reopened.check_integrity()
    finally:
        reopened.close()


def test_repeated_commands_return_original_results_even_after_state_changes(session, financial):
    expense, _, _ = financial
    command = str(uuid4())
    operation = session.ledger.add(replace(expense.draft, note="Повтор"), command_id=command)
    close_command = str(uuid4())
    closed = session.periods.close(MONTH, command_id=close_command)
    assert session.ledger.add(operation.draft, command_id=command) == operation
    opened = session.periods.reopen(MONTH, closed.revision_id, reason="Правка")
    before = snapshot(session)
    assert session.periods.close(MONTH, command_id=close_command) == closed
    assert snapshot(session) == before and session.periods.states() == [opened]
    with pytest.raises(ValidationError):
        session.periods.close(MONTH, reason="Другое", command_id=close_command)


def test_stale_period_command_and_redundant_transitions_are_rejected(session):
    closed = session.periods.close(MONTH)
    for action in (
        lambda: session.periods.close(MONTH),
        lambda: session.periods.close(MONTH, expected_revision_id=closed.revision_id),
        lambda: session.periods.reopen(MONTH, str(uuid4()), reason="Правка"),
        lambda: session.periods.reopen(AccountingMonth(2026, 2), str(uuid4()), reason="Правка"),
    ):
        with pytest.raises(ValidationError):
            action()
    assert len(session.periods.history(MONTH)) == 1


def test_two_closures_accept_only_one_command(session):
    def close():
        try:
            return session.periods.close(MONTH)
        except ValidationError:
            return None

    with ThreadPoolExecutor(max_workers=2) as workers:
        results = list(workers.map(lambda _: close(), range(2)))
    assert sum(value is not None for value in results) == 1
    assert len(session.periods.history(MONTH)) == 1
    session.check_integrity()


@pytest.mark.parametrize("action", ["add", "edit", "move_out", "delete"])
def test_sql_guard_cannot_be_bypassed_by_omitting_application_check(
    session, financial, monkeypatch, action
):
    expense, _, _ = financial
    session.periods.close(MONTH)
    before = snapshot(session)
    monkeypatch.setattr(ledger_repository, "require_open_dates", lambda *args: None)
    with pytest.raises(DatabaseError):
        if action == "delete":
            session.ledger.delete(expense.id, expense.revision_id)
        elif action == "add":
            session.ledger.add(expense.draft)
        else:
            session.ledger.replace(
                expense.id,
                expense.revision_id,
                replace(
                    expense.draft, occurred_on=date(2026, 2, 1) if action == "move_out" else DAY
                ),
            )
    assert snapshot(session) == before
    session.check_integrity()


def test_period_payload_and_parent_are_immutable(session):
    closed = session.periods.close(MONTH)
    for statement in (
        "UPDATE period_revisions SET is_closed=0 WHERE revision_id=:id",
        "DELETE FROM period_revisions WHERE revision_id=:id",
        "INSERT INTO revision_parents(revision_id,parent_id) VALUES (:id,:parent)",
    ):
        with pytest.raises(DatabaseError), session.engine.begin() as c:
            c.execute(text(statement), {"id": closed.revision_id, "parent": str(uuid4())})
    session.check_integrity()


def test_period_failure_rolls_back_entity_sequence_and_journal(session, monkeypatch):
    before = snapshot(session)
    insert = SqlPeriodRepository._insert_payload

    def fail(self, *args):
        insert(self, *args)
        raise OSError("Синтетический отказ")

    monkeypatch.setattr(SqlPeriodRepository, "_insert_payload", fail)
    with pytest.raises(OSError):
        session.periods.close(MONTH)
    assert snapshot(session) == before
    assert session.periods.history(MONTH) == []
    session.check_integrity()


def test_backup_restore_preserves_period_history_and_write_protection(session, financial, tmp_path):
    expense, _, _ = financial
    closed = session.periods.close(MONTH)
    opened = session.periods.reopen(MONTH, closed.revision_id, reason="Проверка")
    again = session.periods.close(MONTH, expected_revision_id=opened.revision_id)
    backup = create_backup(session, tmp_path / "periods.pfbackup", BACKUP_PASSWORD)
    restored, _ = restore_backup(backup, tmp_path / "restored.pfvault", BACKUP_PASSWORD, PASSWORD)
    try:
        assert restored.replica_id != session.replica_id
        assert restored.periods.history(MONTH) == [closed, opened, again]
        with pytest.raises(ValidationError):
            restored.ledger.delete(expense.id, expense.revision_id)
        restored.check_integrity()
    finally:
        restored.close()


def test_old5_is_unchanged_and_only_restored_copy_gets_periods(tmp_path, monkeypatch):
    with monkeypatch.context() as patch:
        patch.setattr(
            vault_module,
            "initialize_schema",
            lambda e: migration_runner.initialize_schema(e, target="0005"),
        )
        source, _ = vault_module.create_vault(
            tmp_path / "old5.pfvault", "Синтетическая старая книга", PASSWORD
        )
    restored = None
    try:
        account = source.catalog.add_account("Счёт", AccountKind.CASH)
        category = next(c.id for c in source.catalog.categories() if c.kind == CategoryKind.EXPENSE)
        expense = source.ledger.add(
            OperationDraft(OperationKind.EXPENSE, DAY, account.id, Money(100), category)
        )
        command = str(uuid4())
        returned = source.ledger.add(
            OperationDraft(
                OperationKind.REFUND,
                DAY,
                account.id,
                Money(50),
                category,
                refund_of=expense.id,
                refund_of_revision_id=expense.revision_id,
            ),
            command_id=command,
        )
        root = source.root
        source.close()
        files = [root / "data" / name for name in ("ledger.sqlite3", "key.json")]
        hashes = [hashlib.sha256(p.read_bytes()).hexdigest() for p in files]
        source = vault_module.open_vault(root, PASSWORD)
        assert source.periods is None and source.ledger.supports_refunds
        backup = create_backup(source, tmp_path / "old5.pfbackup", BACKUP_PASSWORD)
        assert verify_backup(backup, BACKUP_PASSWORD)["schema"] == "0005"
        restored, _ = restore_backup(backup, tmp_path / "new6.pfvault", BACKUP_PASSWORD, PASSWORD)
        assert restored.schema_revision == "0009" and restored.periods is not None
        assert restored.ledger.add(returned.draft, command_id=command) == returned
        restored.periods.close(MONTH)
        assert [hashlib.sha256(p.read_bytes()).hexdigest() for p in files] == hashes
        restored.check_integrity()
    finally:
        source.close()
        if restored:
            restored.close()


def test_closed_session_rejects_period_access(session):
    periods = session.periods
    session.close()
    for action in (periods.states, lambda: periods.close(MONTH), lambda: periods.history(MONTH)):
        with pytest.raises(VaultError):
            action()


def test_integrity_detects_changed_period_hash(session):
    closed = session.periods.close(MONTH)
    with session.engine.begin() as c:
        c.execute(text("DROP TRIGGER revision_no_update"))
        c.execute(
            text("UPDATE entity_revisions SET content_hash=:hash WHERE id=:id"),
            {"hash": "0" * 64, "id": closed.revision_id},
        )
    with pytest.raises(VaultError, match="хешу"):
        session.check_integrity()


def test_closing_serializes_with_financial_write(session, financial, monkeypatch):
    from threading import Event

    expense, _, _ = financial
    ready, release = Event(), Event()
    insert = SqlPeriodRepository._insert_payload

    def pause(self, *args):
        insert(self, *args)
        ready.set()
        assert release.wait(10)

    monkeypatch.setattr(SqlPeriodRepository, "_insert_payload", pause)
    with ThreadPoolExecutor(max_workers=2) as workers:
        closing = workers.submit(session.periods.close, MONTH)
        try:
            assert ready.wait(10)
            writing = workers.submit(
                session.ledger.add, replace(expense.draft, note="Конкурирующая запись")
            )
        finally:
            release.set()
        assert closing.result(timeout=15).is_closed
        with pytest.raises(ValidationError, match="Период"):
            writing.result(timeout=15)
    assert session.ledger.operations() == [expense]
    session.check_integrity()
