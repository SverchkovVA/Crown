import hashlib
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import date, timedelta
from uuid import uuid4

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DatabaseError

from personal_finance.application import vault as vault_module
from personal_finance.domain.errors import ValidationError, VaultError
from personal_finance.domain.ledger import OperationDraft, OperationKind, OperationPart
from personal_finance.domain.models import MAX_MINOR, AccountKind, CategoryKind, Currency, Money
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
def purchase_data(session):
    account = session.catalog.add_account("Проверочная покупка", AccountKind.CASH)
    other = session.catalog.add_account("Другое получение", AccountKind.DEBIT)
    categories = [c.id for c in session.catalog.categories() if c.kind == CategoryKind.EXPENSE]
    purchase = session.ledger.add(
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
    return session.ledger, purchase, other.id, categories


def refund_draft(purchase, minor, category, *, days=1, account=None):
    return OperationDraft(
        OperationKind.REFUND,
        DAY + timedelta(days=days),
        account or purchase.draft.account_id,
        Money(minor),
        category_id=category,
        refund_of=purchase.id,
        refund_of_revision_id=purchase.revision_id,
    )


def account_balance(ledger, account, day=None):
    return next(b.amount.minor for b in ledger.balances(day) if b.account_id == account)


def test_partial_and_full_refunds_reduce_expenses_on_refund_date(session, purchase_data):
    ledger, purchase, _, categories = purchase_data
    source = purchase.draft.account_id
    first = ledger.add(refund_draft(purchase, 6000, categories[0]))
    second = ledger.add(
        replace(
            refund_draft(purchase, 4000, categories[0], days=2),
            category_id=None,
            parts=(
                OperationPart(categories[0], Money(1000)),
                OperationPart(categories[1], Money(3000)),
            ),
        )
    )
    assert account_balance(ledger, source, DAY) == -10000
    assert account_balance(ledger, source, DAY + timedelta(days=1)) == -4000
    assert account_balance(ledger, source, DAY + timedelta(days=2)) == 0
    assert ledger.refund_candidates()[0].amount_remaining == Money(0)
    assert ledger.refund_candidates(excluding_refund_id=first.id)[0].amount_remaining == Money(6000)
    with session.engine.connect() as c:
        roles = list(
            c.execute(
                text(
                    "SELECT l.role,p.amount_minor FROM postings p JOIN ledger_accounts l "
                    "ON l.id=p.ledger_account_id WHERE p.revision_id=:id ORDER BY p.position"
                ),
                {"id": second.revision_id},
            )
        )
        assert [tuple(row) for row in roles] == [
            ("asset", 4000),
            ("expense", -1000),
            ("expense", -3000),
        ]
    session.check_integrity()


def test_refund_can_arrive_on_another_same_currency_account(session, purchase_data):
    ledger, purchase, other, categories = purchase_data
    ledger.add(refund_draft(purchase, 1000, categories[0], account=other))
    assert account_balance(ledger, purchase.draft.account_id) == -10000
    assert account_balance(ledger, other) == 1000
    session.check_integrity()


def test_refund_command_idempotency_and_category_capacity(purchase_data):
    ledger, purchase, _, categories = purchase_data
    command = str(uuid4())
    value = refund_draft(purchase, 7000, categories[0])
    first = ledger.add(value, command_id=command)
    assert ledger.add(value, command_id=command) == first
    with pytest.raises(ValidationError):
        ledger.add(replace(value, amount=Money(1)), command_id=command)
    with pytest.raises(ValidationError, match="Общая сумма возвратов"):
        ledger.add(refund_draft(purchase, 1, categories[0]))
    assert ledger.refund_candidates()[0].amount_remaining == Money(3000)
    assert ledger.history(first.id) == [first]


def test_expense_edit_and_delete_cannot_invalidate_active_refunds(session, purchase_data):
    ledger, purchase, other, categories = purchase_data
    returned = ledger.add(refund_draft(purchase, 6000, categories[0]))
    for value in [
        replace(
            purchase.draft,
            amount=Money(5000),
            parts=(
                OperationPart(categories[0], Money(4000)),
                OperationPart(categories[1], Money(1000)),
            ),
        ),
        replace(purchase.draft, occurred_on=DAY + timedelta(days=2)),
        OperationDraft(
            OperationKind.TRANSFER,
            DAY,
            purchase.draft.account_id,
            Money(10000),
            destination_account_id=other,
        ),
    ]:
        with pytest.raises(ValidationError):
            ledger.replace(purchase.id, purchase.revision_id, value)
    with pytest.raises(ValidationError, match="активными возвратами"):
        ledger.delete(purchase.id, purchase.revision_id)
    assert ledger.history(purchase.id) == [purchase]
    ledger.delete(returned.id, returned.revision_id)
    ledger.delete(purchase.id, purchase.revision_id)
    assert ledger.operations() == []
    assert ledger.history(returned.id)[0].draft.refund_of_revision_id == purchase.revision_id
    session.check_integrity()


def test_expense_change_requires_new_preview_but_keeps_historical_reference(session, purchase_data):
    ledger, purchase, _, categories = purchase_data
    command = str(uuid4())
    returned = ledger.add(refund_draft(purchase, 1000, categories[0]), command_id=command)
    corrected = ledger.replace(
        purchase.id,
        purchase.revision_id,
        replace(
            purchase.draft,
            amount=Money(12000),
            parts=(
                OperationPart(categories[0], Money(8000)),
                OperationPart(categories[1], Money(4000)),
            ),
        ),
    )
    assert ledger.add(returned.draft, command_id=command) == returned
    with pytest.raises(ValidationError, match="расход изменён"):
        ledger.add(refund_draft(purchase, 1000, categories[0]))
    second = ledger.add(refund_draft(corrected, 1000, categories[0]))
    ledger.delete(returned.id, returned.revision_id)
    assert second.draft.refund_of_revision_id == corrected.revision_id
    assert ledger.history(returned.id)[0].draft.refund_of_revision_id == purchase.revision_id
    assert ledger.refund_candidates()[0].amount_remaining == Money(11000)
    session.check_integrity()


@pytest.mark.parametrize("bad", ["date", "category", "identity", "version", "currency"])
def test_bad_refund_reference_is_rejected(session, purchase_data, bad):
    ledger, purchase, _, categories = purchase_data
    value = refund_draft(purchase, 1, categories[0])
    if bad == "date":
        value = replace(value, occurred_on=DAY - timedelta(days=1))
    if bad == "category":
        value = replace(value, category_id=categories[2])
    if bad == "identity":
        value = replace(value, refund_of=str(uuid4()))
    if bad == "version":
        value = replace(value, refund_of_revision_id=str(uuid4()))
    if bad == "currency":
        value = replace(
            value, amount=Money(1, Currency("USD", 2)), fee=Money(0, Currency("USD", 2))
        )
    with pytest.raises(ValidationError):
        ledger.add(value)
    assert ledger.operations() == [purchase]
    session.check_integrity()


def test_income_is_not_a_refund_source(session, purchase_data):
    ledger, purchase, _, categories = purchase_data
    income = next(c.id for c in session.catalog.categories() if c.kind == CategoryKind.INCOME)
    other = ledger.add(
        OperationDraft(OperationKind.INCOME, DAY, purchase.draft.account_id, Money(100), income)
    )
    with pytest.raises(ValidationError):
        ledger.add(refund_draft(other, 1, categories[0]))


def test_concurrent_refunds_cannot_spend_the_same_capacity(session, purchase_data):
    ledger, purchase, _, categories = purchase_data
    value = refund_draft(purchase, 6000, categories[0])

    def attempt():
        try:
            return ledger.add(value)
        except ValidationError:
            return None

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: attempt(), range(2)))
    assert sum(result is not None for result in results) == 1
    assert ledger.refund_candidates()[0].amount_remaining == Money(4000)
    session.check_integrity()


def test_concurrent_repeated_command_produces_one_refund(session, purchase_data):
    ledger, purchase, _, categories = purchase_data
    command = str(uuid4())
    value = refund_draft(purchase, 1000, categories[0])
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: ledger.add(value, command_id=command), range(2)))
    assert results[0] == results[1]
    assert len(ledger.operations()) == 2
    assert ledger.refund_candidates()[0].amount_remaining == Money(9000)
    session.check_integrity()


def test_refund_overflow_rolls_back(session, purchase_data):
    ledger, purchase, other, categories = purchase_data
    ledger.add(OperationDraft(OperationKind.OPENING, DAY, other, Money(MAX_MINOR)))
    before = ledger.operations()
    with pytest.raises(ValidationError):
        ledger.add(refund_draft(purchase, 1, categories[0], account=other))
    assert ledger.operations() == before
    assert account_balance(ledger, other) == MAX_MINOR
    session.check_integrity()


def test_refund_backup_restore_and_historical_source(session, purchase_data, tmp_path):
    ledger, purchase, _, categories = purchase_data
    first = ledger.add(refund_draft(purchase, 2000, categories[0]))
    corrected = ledger.replace(
        first.id, first.revision_id, replace(first.draft, amount=Money(3000))
    )
    backup = create_backup(session, tmp_path / "refund.pfbackup", BACKUP_PASSWORD)
    restored, _ = restore_backup(backup, tmp_path / "restored.pfvault", BACKUP_PASSWORD, PASSWORD)
    try:
        assert restored.replica_id != session.replica_id
        assert restored.ledger.history(first.id) == [first, corrected]
        assert restored.ledger.history(purchase.id) == [purchase]
        assert restored.ledger.refund_candidates()[0].amount_remaining == Money(7000)
        restored.check_integrity()
    finally:
        restored.close()


@pytest.mark.parametrize(
    "statement",
    [
        "UPDATE operation_revisions SET refund_of=NULL WHERE revision_id=:id",
        "UPDATE operation_revisions SET refund_of_revision_id=NULL WHERE revision_id=:id",
        "DELETE FROM operation_revisions WHERE revision_id=:id",
    ],
)
def test_refund_links_are_sealed(session, purchase_data, statement):
    ledger, purchase, _, categories = purchase_data
    first = ledger.add(refund_draft(purchase, 1000, categories[0]))
    with pytest.raises(DatabaseError), session.engine.begin() as c:
        c.execute(text(statement), {"id": first.revision_id})
    session.check_integrity()


def test_failure_after_refund_postings_rolls_back_every_row(session, purchase_data, monkeypatch):
    ledger, purchase, _, categories = purchase_data
    tables = (
        "entities",
        "entity_revisions",
        "operation_revisions",
        "operation_parts",
        "postings",
        "change_groups",
        "change_log",
        "command_receipts",
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

    def fail(self, *args):
        original(self, *args)
        raise OSError("Синтетический отказ возврата")

    monkeypatch.setattr(SqlLedgerRepository, "_insert_postings", fail)
    with pytest.raises(OSError):
        ledger.add(refund_draft(purchase, 1000, categories[0]))
    assert snapshot() == before
    assert ledger.refund_candidates()[0].amount_remaining == Money(10000)
    session.check_integrity()


def test_schema4_source_unchanged_and_only_restored_book_supports_refunds(tmp_path, monkeypatch):
    with monkeypatch.context() as patch:
        patch.setattr(
            vault_module,
            "initialize_schema",
            lambda engine: migration_runner.initialize_schema(engine, target="0004"),
        )
        source, _ = vault_module.create_vault(
            tmp_path / "old4.pfvault", "Проверочная старая книга", PASSWORD
        )
    restored = None
    try:
        account = source.catalog.add_account("Старый счёт", AccountKind.CASH)
        categories = [c.id for c in source.catalog.categories() if c.kind == CategoryKind.EXPENSE]
        command = str(uuid4())
        value = OperationDraft(
            OperationKind.EXPENSE,
            DAY,
            account.id,
            Money(10000),
            parts=(
                OperationPart(categories[0], Money(7000)),
                OperationPart(categories[1], Money(3000)),
            ),
        )
        purchase = source.ledger.add(value, command_id=command)
        source.close()
        files = [source.root / "data" / name for name in ("ledger.sqlite3", "key.json")]
        hashes = [hashlib.sha256(p.read_bytes()).hexdigest() for p in files]
        source = vault_module.open_vault(source.root, PASSWORD)
        assert source.ledger.supports_splits and not source.ledger.supports_refunds
        with pytest.raises(ValidationError, match="в новую папку"):
            source.ledger.add(refund_draft(purchase, 1, categories[0]))
        backup = create_backup(source, tmp_path / "old4.pfbackup", BACKUP_PASSWORD)
        assert verify_backup(backup, BACKUP_PASSWORD)["schema"] == "0004"
        restored, _ = restore_backup(backup, tmp_path / "new5.pfvault", BACKUP_PASSWORD, PASSWORD)
        assert restored.schema_revision == "0012" and restored.ledger.supports_refunds
        assert restored.ledger.add(value, command_id=command) == purchase
        restored.ledger.add(refund_draft(purchase, 1000, categories[0]))
        assert restored.ledger.refund_candidates()[0].amount_remaining == Money(9000)
        restored.check_integrity()
        assert [hashlib.sha256(p.read_bytes()).hexdigest() for p in files] == hashes
        source.check_integrity()
    finally:
        source.close()
        if restored is not None:
            restored.close()


def test_foreign_book_purchase_is_not_a_refund_source(session, purchase_data, tmp_path):
    ledger, purchase, _, categories = purchase_data
    foreign, _ = vault_module.create_vault(
        tmp_path / "foreign.pfvault", "Другая тестовая книга", PASSWORD
    )
    try:
        account = foreign.catalog.add_account("Чужой счёт", AccountKind.CASH)
        category = next(
            c.id for c in foreign.catalog.categories() if c.kind == CategoryKind.EXPENSE
        )
        other = foreign.ledger.add(
            OperationDraft(OperationKind.EXPENSE, DAY, account.id, Money(100), category)
        )
        value = replace(
            refund_draft(purchase, 1, categories[0]),
            refund_of=other.id,
            refund_of_revision_id=other.revision_id,
        )
        with pytest.raises(ValidationError):
            ledger.add(value)
        session.check_integrity()
    finally:
        foreign.close()


def test_refund_edit_uses_its_capacity_and_deletion_releases_it(session, purchase_data):
    ledger, purchase, _, categories = purchase_data
    first = ledger.add(refund_draft(purchase, 7000, categories[0]))
    assert ledger.refund_candidates(excluding_refund_id=first.id)[0].amount_remaining == Money(
        10000
    )
    corrected = ledger.replace(
        first.id, first.revision_id, replace(first.draft, amount=Money(6000))
    )
    assert ledger.refund_candidates()[0].amount_remaining == Money(4000)
    with pytest.raises(ValidationError):
        ledger.replace(first.id, first.revision_id, first.draft)
    ledger.delete(corrected.id, corrected.revision_id)
    assert ledger.refund_candidates()[0].amount_remaining == Money(10000)
    assert len(ledger.history(first.id)) == 3
    session.check_integrity()


def test_database_refuses_wrong_historical_link_before_seal(session, purchase_data, monkeypatch):
    ledger, purchase, _, categories = purchase_data
    income = next(c.id for c in session.catalog.categories() if c.kind == CategoryKind.INCOME)
    incoming = ledger.add(
        OperationDraft(OperationKind.INCOME, DAY, purchase.draft.account_id, Money(100), income)
    )
    before = ledger.operations()
    insert = SqlLedgerRepository._insert_postings

    def corrupt(self, connection, revision_id, value):
        insert(self, connection, revision_id, value)
        connection.execute(
            text(
                "UPDATE operation_revisions SET refund_of_revision_id=:source WHERE revision_id=:id"
            ),
            {"source": incoming.revision_id, "id": revision_id},
        )

    monkeypatch.setattr(SqlLedgerRepository, "_insert_postings", corrupt)
    with pytest.raises(DatabaseError):
        ledger.add(refund_draft(purchase, 1000, categories[0]))
    assert ledger.operations() == before
    session.check_integrity()


def test_failed_schema4_to5_restore_preserves_source_and_backup(tmp_path, monkeypatch):
    with monkeypatch.context() as patch:
        patch.setattr(
            vault_module,
            "initialize_schema",
            lambda engine: migration_runner.initialize_schema(engine, target="0004"),
        )
        source, _ = vault_module.create_vault(
            tmp_path / "source4.pfvault", "Тест отказа миграции", PASSWORD
        )
    try:
        account = source.catalog.add_account("Счёт", AccountKind.CASH)
        category = next(c.id for c in source.catalog.categories() if c.kind == CategoryKind.EXPENSE)
        source.ledger.add(
            OperationDraft(OperationKind.EXPENSE, DAY, account.id, Money(100), category)
        )
        source.close()
        source = vault_module.open_vault(source.root, PASSWORD)
        backup = create_backup(source, tmp_path / "source.pfbackup", BACKUP_PASSWORD)
        files = [source.root / "data" / name for name in ("ledger.sqlite3", "key.json")] + [backup]
        before = [hashlib.sha256(p.read_bytes()).hexdigest() for p in files]
        upgrade = migration_runner.command.upgrade

        def fail(*args, **kwargs):
            upgrade(*args, **kwargs)
            raise OSError("Синтетический отказ после 0005")

        target = tmp_path / "failed.pfvault"
        with monkeypatch.context() as patch:
            patch.setattr(migration_runner.command, "upgrade", fail)
            with pytest.raises(OSError):
                restore_backup(backup, target, BACKUP_PASSWORD, PASSWORD)
        assert not target.exists()
        assert [hashlib.sha256(p.read_bytes()).hexdigest() for p in files] == before
        source.check_integrity()
    finally:
        source.close()


def test_refund_can_be_relinked_and_releases_old_purchase_capacity(session, purchase_data):
    ledger, purchase, other, categories = purchase_data
    second = ledger.add(
        OperationDraft(OperationKind.EXPENSE, DAY, other, Money(5000), categories[0])
    )
    returned = ledger.add(refund_draft(purchase, 2000, categories[0]))
    corrected = ledger.replace(
        returned.id,
        returned.revision_id,
        replace(returned.draft, refund_of=second.id, refund_of_revision_id=second.revision_id),
    )
    capacities = {
        candidate.expense.id: candidate.amount_remaining.minor
        for candidate in ledger.refund_candidates()
    }
    assert capacities == {purchase.id: 10000, second.id: 3000}
    assert ledger.history(returned.id) == [returned, corrected]
    session.check_integrity()


def test_correction_may_reclassify_only_unreturned_part(session, purchase_data):
    ledger, purchase, _, categories = purchase_data
    ledger.add(refund_draft(purchase, 6000, categories[0]))
    corrected = ledger.replace(
        purchase.id,
        purchase.revision_id,
        replace(
            purchase.draft,
            parts=(
                OperationPart(categories[0], Money(7000)),
                OperationPart(categories[2], Money(3000)),
            ),
        ),
    )
    assert ledger.refund_candidates()[0].remaining_parts == (
        OperationPart(categories[0], Money(1000)),
        OperationPart(categories[2], Money(3000)),
    )
    ledger.add(refund_draft(corrected, 3000, categories[2]))
    session.check_integrity()


def test_refund_cannot_replace_its_own_source(session, purchase_data):
    ledger, purchase, _, categories = purchase_data
    with pytest.raises(ValidationError):
        ledger.replace(
            purchase.id, purchase.revision_id, refund_draft(purchase, 1000, categories[0])
        )
    assert ledger.history(purchase.id) == [purchase]
    session.check_integrity()


def test_closed_session_cannot_query_refund_capacity(session, purchase_data):
    ledger, _, _, _ = purchase_data
    session.close()
    with pytest.raises(VaultError):
        ledger.refund_candidates()
