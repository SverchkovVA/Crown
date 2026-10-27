from dataclasses import replace
from datetime import date, timedelta
from uuid import uuid4

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DatabaseError

from personal_finance.application.vault import create_vault
from personal_finance.domain.errors import FinanceError, ValidationError, VaultError
from personal_finance.domain.ledger import OperationDraft, OperationKind
from personal_finance.domain.models import MAX_MINOR, AccountKind, CategoryKind, Currency, Money
from personal_finance.infrastructure.backup.container import create_backup, restore_backup
from personal_finance.infrastructure.persistence.ledger_repository import SqlLedgerRepository
from tests.conftest import BACKUP_PASSWORD, PASSWORD

DAY = date(2026, 10, 26)


@pytest.fixture
def journal(session):
    source = session.catalog.add_account("Источник", AccountKind.CASH)
    target = session.catalog.add_account("Получатель", AccountKind.DEBIT)
    income = next(c for c in session.catalog.categories() if c.kind == CategoryKind.INCOME)
    expense = next(c for c in session.catalog.categories() if c.kind == CategoryKind.EXPENSE)
    assert session.ledger is not None
    return session.ledger, source.id, target.id, income.id, expense.id


def balances(ledger, as_of=None):
    return {item.account_id: item.amount.minor for item in ledger.balances(as_of)}


def test_opening_income_expense_transfer_and_as_of_balance(journal, session):
    ledger, source, target, income, expense = journal
    assert balances(ledger) == {source: 0, target: 0}
    ledger.add(OperationDraft(OperationKind.OPENING, DAY, source, Money(100000)))
    ledger.add(
        OperationDraft(OperationKind.INCOME, DAY + timedelta(days=1), source, Money(5000), income)
    )
    ledger.add(
        OperationDraft(OperationKind.EXPENSE, DAY + timedelta(days=1), source, Money(1500), expense)
    )
    ledger.add(
        OperationDraft(
            OperationKind.TRANSFER,
            DAY + timedelta(days=2),
            source,
            Money(10000),
            destination_account_id=target,
            fee=Money(100),
            fee_category_id=expense,
        )
    )
    assert balances(ledger, DAY - timedelta(days=1)) == {source: 0, target: 0}
    assert balances(ledger, DAY) == {source: 100000, target: 0}
    assert balances(ledger) == {source: 93400, target: 10000}
    assert len(ledger.operations()) == 4
    session.check_integrity()


def test_revisions_replace_effect_and_delete_preserves_history(journal, session):
    ledger, source, _, income, _ = journal
    first = ledger.add(OperationDraft(OperationKind.INCOME, DAY, source, Money(1000), income))
    second = ledger.replace(first.id, first.revision_id, replace(first.draft, amount=Money(2000)))
    assert second.id == first.id and second.revision_id != first.revision_id
    assert balances(ledger)[source] == 2000
    with pytest.raises(ValidationError, match="уже изменена"):
        ledger.replace(first.id, first.revision_id, first.draft)
    command = str(uuid4())
    deleted = ledger.delete(second.id, second.revision_id, command_id=command)
    assert ledger.delete(second.id, second.revision_id, command_id=command) == deleted
    assert deleted.is_deleted
    assert ledger.operations() == []
    assert ledger.operations(include_deleted=True) == [deleted]
    assert balances(ledger)[source] == 0
    assert ledger.history(first.id) == [first, second, deleted]
    with session.engine.connect() as connection:
        assert connection.execute(text("SELECT count(*) FROM revision_parents")).scalar_one() == 2
    session.check_integrity()


def test_repeated_command_does_not_duplicate_and_other_payload_is_rejected(journal):
    ledger, source, _, income, _ = journal
    command = str(uuid4())
    draft = OperationDraft(OperationKind.INCOME, DAY, source, Money(1000), income)
    first = ledger.add(draft, command_id=command)
    assert ledger.add(draft, command_id=command) == first
    with pytest.raises(ValidationError, match="других данных"):
        ledger.add(replace(draft, amount=Money(2000)), command_id=command)
    assert balances(ledger)[source] == 1000
    assert len(ledger.operations()) == 1


def test_rejected_references_and_currency_do_not_publish_operations(journal):
    ledger, source, _, income, expense = journal
    invalid = [
        OperationDraft(OperationKind.INCOME, DAY, str(uuid4()), Money(100), income),
        OperationDraft(OperationKind.INCOME, DAY, source, Money(100), expense),
        OperationDraft(OperationKind.EXPENSE, DAY, source, Money(100), income),
        OperationDraft(
            OperationKind.INCOME,
            DAY,
            source,
            Money(100, Currency("USD", 2)),
            income,
            fee=Money(0, Currency("USD", 2)),
        ),
    ]
    for draft in invalid:
        with pytest.raises(ValidationError):
            ledger.add(draft)
    assert ledger.operations() == []
    assert balances(ledger)[source] == 0


def test_foreign_book_reference_is_rejected(journal, tmp_path):
    ledger, source, _, income, _ = journal
    foreign, _ = create_vault(tmp_path / "foreign.pfvault", "Другая книга", PASSWORD)
    try:
        account = foreign.catalog.add_account("Чужой счёт", AccountKind.CASH)
        with pytest.raises(ValidationError):
            ledger.add(OperationDraft(OperationKind.INCOME, DAY, account.id, Money(100), income))
    finally:
        foreign.close()
    assert balances(ledger)[source] == 0


def test_opening_is_unique_and_financial_dates_cannot_precede_it(journal):
    ledger, source, _, income, _ = journal
    opening = ledger.add(OperationDraft(OperationKind.OPENING, DAY, source, Money(-100)))
    with pytest.raises(ValidationError, match="уже есть"):
        ledger.add(OperationDraft(OperationKind.OPENING, DAY, source, Money(0)))
    with pytest.raises(ValidationError, match="раньше"):
        ledger.add(
            OperationDraft(
                OperationKind.INCOME, DAY - timedelta(days=1), source, Money(100), income
            )
        )
    ledger.replace(opening.id, opening.revision_id, replace(opening.draft, amount=Money(0)))
    assert balances(ledger)[source] == 0


def test_overflow_rolls_back_complete_financial_group(journal, session):
    ledger, source, _, income, _ = journal
    ledger.add(OperationDraft(OperationKind.OPENING, DAY, source, Money(MAX_MINOR)))
    with pytest.raises(ValidationError, match="диапазон"):
        ledger.add(OperationDraft(OperationKind.INCOME, DAY, source, Money(1), income))
    assert balances(ledger)[source] == MAX_MINOR
    assert len(ledger.operations()) == 1
    session.check_integrity()


def test_failure_after_postings_rolls_back_revisions_commands_and_sequence(
    journal, session, monkeypatch
):
    ledger, source, _, income, _ = journal
    tables = [
        "entities",
        "entity_revisions",
        "change_groups",
        "change_log",
        "command_receipts",
        "ledger_accounts",
        "operation_revisions",
        "postings",
    ]

    def snapshot():
        with session.engine.connect() as connection:
            counts = {
                table: connection.execute(text(f"SELECT count(*) FROM {table}")).scalar_one()
                for table in tables
            }
            counts["sequence"] = connection.execute(
                text("SELECT sequence FROM replicas WHERE id=:id"),
                {"id": session.replica_id},
            ).scalar_one()
            return counts

    original = SqlLedgerRepository._insert_postings

    def fail(self, *args):
        original(self, *args)
        raise OSError("Синтетический сбой после проводок")

    before = snapshot()
    monkeypatch.setattr(SqlLedgerRepository, "_insert_postings", fail)
    with pytest.raises(OSError):
        ledger.add(OperationDraft(OperationKind.INCOME, DAY, source, Money(100), income))
    assert snapshot() == before
    assert balances(ledger)[source] == 0
    session.check_integrity()


@pytest.mark.parametrize(
    "statement",
    [
        "UPDATE operation_revisions SET note='изменено' WHERE revision_id=:id",
        "DELETE FROM operation_revisions WHERE revision_id=:id",
        "UPDATE postings SET amount_minor=1 WHERE revision_id=:id",
        "DELETE FROM postings WHERE revision_id=:id",
        "INSERT INTO postings(revision_id,position,ledger_account_id,amount_minor) "
        "SELECT revision_id,99,ledger_account_id,0 FROM postings WHERE revision_id=:id LIMIT 1",
    ],
)
def test_sealed_financial_rows_are_immutable(journal, session, statement):
    ledger, source, _, income, _ = journal
    operation = ledger.add(OperationDraft(OperationKind.INCOME, DAY, source, Money(100), income))
    with pytest.raises(DatabaseError), session.engine.begin() as connection:
        connection.execute(text(statement), {"id": operation.revision_id})
    assert balances(ledger)[source] == 100
    session.check_integrity()


def test_financial_payload_hash_detects_tampering(journal, session):
    ledger, source, _, income, _ = journal
    operation = ledger.add(OperationDraft(OperationKind.INCOME, DAY, source, Money(100), income))
    with session.engine.begin() as connection:
        connection.exec_driver_sql("DROP TRIGGER operation_revisions_no_update")
        connection.execute(
            text("UPDATE operation_revisions SET note='подмена' WHERE revision_id=:id"),
            {"id": operation.revision_id},
        )
    with pytest.raises(VaultError, match="хешу"):
        session.check_integrity()


def test_backup_restores_journal_balances_and_new_replica(journal, session, tmp_path):
    ledger, source, target, income, _ = journal
    operation = ledger.add(OperationDraft(OperationKind.INCOME, DAY, source, Money(1000), income))
    ledger.replace(
        operation.id, operation.revision_id, replace(operation.draft, amount=Money(2000))
    )
    ledger.add(
        OperationDraft(
            OperationKind.TRANSFER, DAY, source, Money(500), destination_account_id=target
        )
    )
    backup = create_backup(session, tmp_path / "journal.pfbackup", BACKUP_PASSWORD)
    restored, _ = restore_backup(
        backup,
        tmp_path / "journal-restored.pfvault",
        BACKUP_PASSWORD,
        "новый проверочный пароль журнала",
    )
    try:
        assert restored.ledger is not None
        assert balances(restored.ledger) == balances(ledger)
        assert restored.ledger.operations() == ledger.operations()
        assert len(restored.ledger.history(operation.id)) == 2
        assert restored.replica_id != session.replica_id
        restored.ledger.add(OperationDraft(OperationKind.INCOME, DAY, target, Money(1), income))
        restored.check_integrity()
        assert balances(ledger)[target] == 500
    finally:
        restored.close()


def test_saved_ledger_cannot_write_after_close(journal, session):
    ledger, source, _, income, _ = journal
    session.close()
    with pytest.raises(FinanceError, match="закрыта"):
        ledger.add(OperationDraft(OperationKind.INCOME, DAY, source, Money(100), income))


def test_overflow_on_earlier_date_is_rejected_even_when_current_total_fits(journal):
    ledger, source, _, income, expense = journal
    ledger.add(OperationDraft(OperationKind.INCOME, DAY, source, Money(MAX_MINOR), income))
    ledger.add(
        OperationDraft(
            OperationKind.EXPENSE,
            DAY + timedelta(days=1),
            source,
            Money(MAX_MINOR),
            expense,
        )
    )
    assert balances(ledger)[source] == 0
    with pytest.raises(ValidationError, match="диапазон"):
        ledger.add(OperationDraft(OperationKind.INCOME, DAY, source, Money(1), income))
    assert balances(ledger, DAY)[source] == MAX_MINOR
    assert len(ledger.operations()) == 2


def test_history_order_uses_parents_when_clock_moves_backwards(journal, monkeypatch):
    from personal_finance.infrastructure.persistence import ledger_repository as module

    ledger, source, _, income, _ = journal
    monkeypatch.setattr(module, "utc_now", lambda: "2026-10-26T12:00:00Z")
    first = ledger.add(OperationDraft(OperationKind.INCOME, DAY, source, Money(100), income))
    monkeypatch.setattr(module, "utc_now", lambda: "2026-10-25T12:00:00Z")
    second = ledger.replace(first.id, first.revision_id, replace(first.draft, amount=Money(200)))
    assert ledger.history(first.id) == [first, second]
    ledger.check_integrity()


def test_sealed_parent_link_cannot_be_deleted(journal, session):
    ledger, source, _, income, _ = journal
    first = ledger.add(OperationDraft(OperationKind.INCOME, DAY, source, Money(100), income))
    second = ledger.replace(first.id, first.revision_id, replace(first.draft, amount=Money(200)))
    with pytest.raises(DatabaseError), session.engine.begin() as connection:
        connection.execute(
            text("DELETE FROM revision_parents WHERE revision_id=:id"), {"id": second.revision_id}
        )
    session.check_integrity()
