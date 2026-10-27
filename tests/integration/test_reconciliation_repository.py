from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import date, timedelta
from threading import Event
from uuid import uuid4

import pytest
from sqlalchemy import text

from personal_finance.application import vault as vault_module
from personal_finance.domain.errors import ValidationError, VaultError
from personal_finance.domain.ledger import OperationDraft, OperationKind, OperationPart
from personal_finance.domain.models import MAX_MINOR, AccountKind, CategoryKind, Currency, Money
from personal_finance.domain.periods import AccountingMonth
from personal_finance.infrastructure.backup.container import create_backup, restore_backup
from personal_finance.infrastructure.persistence import migration_runner
from personal_finance.infrastructure.persistence.ledger_repository import SqlLedgerRepository
from tests.conftest import BACKUP_PASSWORD, PASSWORD

DAY = date(2026, 1, 15)


@pytest.fixture
def data(session):
    account = session.catalog.add_account("Проверочный счёт", AccountKind.CASH)
    target = session.catalog.add_account("Проверочный получатель", AccountKind.CASH)
    expense = [c.id for c in session.catalog.categories() if c.kind == CategoryKind.EXPENSE]
    income = next(c.id for c in session.catalog.categories() if c.kind == CategoryKind.INCOME)
    ledger = session.ledger
    opening = ledger.add(OperationDraft(OperationKind.OPENING, DAY, account.id, Money(100000)))
    incoming = ledger.add(
        OperationDraft(OperationKind.INCOME, DAY, account.id, Money(10000), income)
    )
    spending = ledger.add(
        OperationDraft(
            OperationKind.EXPENSE,
            DAY,
            account.id,
            Money(3000),
            parts=(OperationPart(expense[0], Money(1000)), OperationPart(expense[1], Money(2000))),
        )
    )
    transfer = ledger.add(
        OperationDraft(
            OperationKind.TRANSFER,
            DAY,
            account.id,
            Money(20000),
            destination_account_id=target.id,
            fee=Money(100),
            fee_category_id=expense[0],
        )
    )
    returned = ledger.add(
        OperationDraft(
            OperationKind.REFUND,
            DAY + timedelta(days=1),
            account.id,
            Money(1000),
            expense[0],
            refund_of=spending.id,
            refund_of_revision_id=spending.revision_id,
        )
    )
    return account, target, income, [opening, incoming, spending, transfer, returned]


def state(session):
    with session.engine.connect() as c:
        return [
            c.execute(text("SELECT count(*) FROM " + name)).scalar_one()
            for name in (
                "entities",
                "entity_revisions",
                "change_log",
                "command_receipts",
                "postings",
                "period_revisions",
            )
        ] + list(c.execute(text("SELECT sequence FROM replicas ORDER BY id")).scalars())


def test_reconciliation_includes_both_sides_commission_parts_refund_and_date(session, data):
    account, target, _, operations = data
    before = state(session)
    result = session.ledger.reconcile(account.id, DAY + timedelta(days=1), Money(87900))
    assert result.matches and result.book_balance == Money(87900)
    assert {line.operation.id: line.change.minor for line in result.lines} == dict(
        zip(
            [operation.id for operation in operations],
            [100000, 10000, -3000, -20100, 1000],
            strict=True,
        )
    )
    assert session.ledger.reconcile(account.id, DAY, Money(86900)).matches
    other = session.ledger.reconcile(target.id, DAY, Money(20000))
    assert other.matches and len(other.lines) == 1 and other.lines[0].change == Money(20000)
    assert session.ledger.reconcile(account.id, DAY - timedelta(days=1), Money(0)).lines == ()
    assert state(session) == before
    session.check_integrity()


@pytest.mark.parametrize("difference", [-123, 0, 456])
def test_discrepancy_does_not_create_adjustments(session, data, difference):
    account, _, _, _ = data
    before = state(session)
    result = session.ledger.reconcile(account.id, DAY, Money(86900 + difference))
    assert result.difference_minor == difference and result.matches == (difference == 0)
    assert state(session) == before


def test_only_current_active_versions_and_corrected_dates_are_counted(session, data):
    account, _, _, operations = data
    incoming = operations[1]
    corrected = session.ledger.replace(
        incoming.id,
        incoming.revision_id,
        replace(incoming.draft, amount=Money(5000), occurred_on=DAY + timedelta(days=2)),
    )
    assert session.ledger.reconcile(account.id, DAY, Money(76900)).matches
    session.ledger.delete(corrected.id, corrected.revision_id)
    result = session.ledger.reconcile(account.id, DAY + timedelta(days=10), Money(77900))
    assert result.matches and all(line.operation.id != incoming.id for line in result.lines)


def test_closed_month_can_be_reconciled_without_reopening(session, data):
    account, _, _, _ = data
    closed = session.periods.close(AccountingMonth.from_date(DAY))
    before = state(session)
    assert session.ledger.reconcile(account.id, DAY, Money(86900)).matches
    assert state(session) == before and session.periods.states() == [closed]


@pytest.mark.parametrize("case", ["missing", "currency", "date", "amount"])
def test_invalid_request_is_rejected_without_writes(session, data, case):
    account, _, _, _ = data
    args = [account.id, DAY, Money(0)]
    if case == "missing":
        args[0] = str(uuid4())
    elif case == "currency":
        args[2] = Money(0, Currency("USD", 2))
    elif case == "date":
        args[1] = "2026-01-15"
    else:
        args[2] = 0.1
    before = state(session)
    with pytest.raises(ValidationError):
        session.ledger.reconcile(*args)
    assert state(session) == before


def test_extreme_difference_and_empty_account(session):
    account = session.catalog.add_account("Крайний счёт", AccountKind.CASH)
    assert session.ledger.reconcile(account.id, DAY, Money(-1)).book_balance == Money(0)
    session.ledger.add(OperationDraft(OperationKind.OPENING, DAY, account.id, Money(-MAX_MINOR)))
    result = session.ledger.reconcile(account.id, DAY, Money(MAX_MINOR))
    assert result.difference_minor == 2 * MAX_MINOR


def test_read_snapshot_serializes_with_write(session, data, monkeypatch):
    account, _, income, _ = data
    ready, release = Event(), Event()
    balances = SqlLedgerRepository._balances

    def pause(self, *args):
        value = balances(self, *args)
        ready.set()
        assert release.wait(10)
        return value

    monkeypatch.setattr(SqlLedgerRepository, "_balances", pause)
    with ThreadPoolExecutor(max_workers=2) as workers:
        reading = workers.submit(session.ledger.reconcile, account.id, DAY, Money(86900))
        try:
            assert ready.wait(10)
            writing = workers.submit(
                session.ledger.add,
                OperationDraft(OperationKind.INCOME, DAY, account.id, Money(100), income),
            )
        finally:
            release.set()
        result = reading.result(timeout=15)
        writing.result(timeout=15)
    assert result.matches and sum(line.change.minor for line in result.lines) == 86900
    assert session.ledger.reconcile(account.id, DAY, Money(87000)).matches


def test_restore_keeps_same_reconciliation_result(session, data, tmp_path):
    account, _, _, _ = data
    expected = session.ledger.reconcile(account.id, DAY, Money(86900))
    backup = create_backup(session, tmp_path / "test.pfbackup", BACKUP_PASSWORD)
    restored, _ = restore_backup(backup, tmp_path / "restored.pfvault", BACKUP_PASSWORD, PASSWORD)
    try:
        assert restored.ledger.reconcile(account.id, DAY, Money(86900)) == expected
    finally:
        restored.close()


def test_schema3_has_reconciliation_without_migration(tmp_path, monkeypatch):
    with monkeypatch.context() as patch:
        patch.setattr(
            vault_module,
            "initialize_schema",
            lambda e: migration_runner.initialize_schema(e, target="0003"),
        )
        source, _ = vault_module.create_vault(
            tmp_path / "old.pfvault", "Старая тестовая книга", PASSWORD
        )
    try:
        account = source.catalog.add_account("Наличные", AccountKind.CASH)
        source.ledger.add(OperationDraft(OperationKind.OPENING, DAY, account.id, Money(123)))
        assert source.ledger.reconcile(account.id, DAY, Money(123)).matches
        assert source.schema_revision == "0003"
    finally:
        source.close()


def test_closed_session_rejects_reconciliation(session, data):
    account, _, _, _ = data
    ledger = session.ledger
    session.close()
    with pytest.raises(VaultError):
        ledger.reconcile(account.id, DAY, Money(0))
