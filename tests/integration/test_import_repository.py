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
from personal_finance.domain.imports import (
    ImportDocument,
    ImportKind,
    ImportRow,
    ImportSelection,
    ImportStatus,
    account_movement,
    digest_json,
)
from personal_finance.domain.ledger import OperationDraft, OperationKind
from personal_finance.domain.models import MAX_MINOR, AccountKind, CategoryKind, Money
from personal_finance.domain.periods import AccountingMonth
from personal_finance.infrastructure.backup.container import (
    create_backup,
    restore_backup,
    verify_backup,
)
from personal_finance.infrastructure.persistence import migration_runner
from personal_finance.infrastructure.persistence.import_repository import SqlImportRepository
from tests.conftest import BACKUP_PASSWORD, PASSWORD

DAY = date(2026, 1, 15)
TABLES = (
    "entities",
    "entity_revisions",
    "change_groups",
    "revision_parents",
    "revision_heads",
    "change_log",
    "command_receipts",
    "operation_revisions",
    "postings",
    "import_batches",
    "import_items",
)


@pytest.fixture
def setup_import(session):
    account = session.catalog.add_account("Счёт импорта", AccountKind.CASH)
    categories = {
        kind: next(c.id for c in session.catalog.categories() if c.kind == kind)
        for kind in CategoryKind
    }
    return account, categories


def document(*amounts, ids=True, day=DAY):
    rows = tuple(
        ImportRow(i, day, Money(amount), f"Тестовая строка {i}", f"test-{i}" if ids else None)
        for i, amount in enumerate(amounts, 1)
    )
    return ImportDocument(
        ImportKind.CSV, digest_json([r.as_dict() for r in rows]), digest_json("csv-v1"), rows
    )


def selections(doc, account, categories, *, ack=False):
    return tuple(
        ImportSelection(
            row.position,
            OperationDraft(
                OperationKind.INCOME if row.amount.minor > 0 else OperationKind.EXPENSE,
                row.occurred_on,
                account.id,
                Money(abs(row.amount.minor)),
                categories[CategoryKind.INCOME if row.amount.minor > 0 else CategoryKind.EXPENSE],
                note=row.note,
            ),
            ack,
        )
        for row in doc.rows
    )


def counts(session):
    with session.engine.connect() as c:
        return [
            c.execute(text("SELECT count(*) FROM " + table)).scalar_one() for table in TABLES
        ] + list(c.execute(text("SELECT sequence FROM replicas ORDER BY id")).scalars())


def test_preview_is_read_only_and_confirm_shares_one_group(session, setup_import):
    account, categories = setup_import
    doc = document(-1000, 200)
    before = counts(session)
    preview = session.importing.preview(doc, account.id)
    assert all(line.status == ImportStatus.NEW for line in preview.lines)
    assert counts(session) == before and not session.ledger.operations()
    result = session.importing.confirm(doc, account.id, selections(doc, account, categories))
    assert result.linked_count == 0 and len(result.operations) == 2
    assert session.ledger.balances()[0].amount == Money(-800)
    with session.engine.connect() as c:
        assert (
            c.execute(
                text(
                    "SELECT count(DISTINCT group_id) FROM entity_revisions "
                    "WHERE id IN (SELECT revision_id FROM import_items)"
                )
            ).scalar_one()
            == 1
        )
    session.check_integrity()


def test_replay_returns_original_snapshots_after_edit_and_delete(session, setup_import):
    account, categories = setup_import
    doc = document(-1000, 200)
    command = str(uuid4())
    selected = selections(doc, account, categories)
    result = session.importing.confirm(doc, account.id, selected, command_id=command)
    first, second = result.operations
    session.ledger.replace(first.id, first.revision_id, replace(first.draft, note="Исправлено"))
    session.ledger.delete(second.id, second.revision_id)
    before = counts(session)
    assert session.importing.confirm(doc, account.id, selected, command_id=command) == result
    assert counts(session) == before
    with pytest.raises(ValidationError, match="других данных"):
        session.importing.confirm(
            doc,
            account.id,
            (replace(selected[0], acknowledged_duplicate=True), selected[1]),
            command_id=command,
        )
    assert all(
        line.status == ImportStatus.DUPLICATE
        for line in session.importing.preview(doc, account.id).lines
    )
    session.check_integrity()


def test_external_id_dedup_survives_reordered_file_and_conflicting_id_is_not_overwritten(
    session, setup_import
):
    account, categories = setup_import
    doc = document(-1000, 200)
    session.importing.confirm(doc, account.id, selections(doc, account, categories))
    changed = replace(
        doc,
        file_hash=digest_json("reordered"),
        rows=(replace(doc.rows[1], position=1), replace(doc.rows[0], position=2)),
    )
    assert all(
        line.status == ImportStatus.DUPLICATE
        for line in session.importing.preview(changed, account.id).lines
    )
    conflict = replace(doc, rows=(replace(doc.rows[0], amount=Money(-1100)), doc.rows[1]))
    assert session.importing.preview(conflict, account.id).lines[0].status == ImportStatus.CONFLICT
    before = counts(session)
    with pytest.raises(ValidationError):
        session.importing.confirm(conflict, account.id, selections(conflict, account, categories))
    assert counts(session) == before


def test_identical_rows_without_external_id_remain_separate_after_explicit_ack(
    session, setup_import
):
    account, categories = setup_import
    doc = document(-100, -100, ids=False)
    assert all(
        line.status == ImportStatus.POSSIBLE
        for line in session.importing.preview(doc, account.id).lines
    )
    before = counts(session)
    with pytest.raises(ValidationError, match="повтора"):
        session.importing.confirm(doc, account.id, selections(doc, account, categories))
    assert counts(session) == before
    result = session.importing.confirm(
        doc, account.id, selections(doc, account, categories, ack=True)
    )
    assert len({op.id for op in result.operations}) == 2
    assert session.ledger.balances()[0].amount == Money(-200)
    assert all(
        line.status == ImportStatus.DUPLICATE
        for line in session.importing.preview(doc, account.id).lines
    )
    session.check_integrity()


def test_manual_operation_can_be_linked_without_new_financial_version(session, setup_import):
    account, categories = setup_import
    doc = document(-100)
    selected = selections(doc, account, categories)[0]
    operation = session.ledger.add(selected.draft)
    line = session.importing.preview(doc, account.id).lines[0]
    assert line.status == ImportStatus.POSSIBLE and line.candidates == (operation,)
    linked = replace(
        selected, existing_operation_id=operation.id, expected_revision_id=operation.revision_id
    )
    before = session.ledger.history(operation.id)
    result = session.importing.confirm(doc, account.id, (linked,))
    assert result.linked_count == 1 and result.operations == (operation,)
    assert session.ledger.history(operation.id) == before and len(session.ledger.operations()) == 1
    session.check_integrity()


def test_both_bank_sides_can_link_same_transfer_without_duplicate_money(session, setup_import):
    account, categories = setup_import
    other = session.catalog.add_account("Получатель", AccountKind.CASH)
    transfer = session.ledger.add(
        OperationDraft(
            OperationKind.TRANSFER,
            DAY,
            account.id,
            Money(1000),
            destination_account_id=other.id,
            fee=Money(10),
            fee_category_id=categories[CategoryKind.EXPENSE],
        )
    )
    for target, delta in ((account, -1010), (other, 1000)):
        doc = document(delta)
        linked = ImportSelection(
            1,
            transfer.draft,
            existing_operation_id=transfer.id,
            expected_revision_id=transfer.revision_id,
        )
        assert session.importing.confirm(doc, target.id, (linked,)).linked_count == 1
    assert len(session.ledger.operations()) == 1
    assert {b.account_id: b.amount.minor for b in session.ledger.balances()} == {
        account.id: -1010,
        other.id: 1000,
    }
    session.check_integrity()


def test_import_can_create_transfer_and_linked_refund_through_normal_rules(session, setup_import):
    account, categories = setup_import
    other = session.catalog.add_account("Второй", AccountKind.CASH)
    purchase = session.ledger.add(selections(document(-1000), account, categories)[0].draft)
    doc = document(200, -300)
    refund = OperationDraft(
        OperationKind.REFUND,
        DAY,
        account.id,
        Money(200),
        categories[CategoryKind.EXPENSE],
        refund_of=purchase.id,
        refund_of_revision_id=purchase.revision_id,
    )
    transfer = OperationDraft(
        OperationKind.TRANSFER, DAY, account.id, Money(300), destination_account_id=other.id
    )
    result = session.importing.confirm(
        doc, account.id, (ImportSelection(1, refund), ImportSelection(2, transfer))
    )
    assert len(result.operations) == 2 and account_movement(refund, account.id) == 200
    assert session.ledger.refund_candidates()[0].amount_remaining.minor == 800
    session.check_integrity()


@pytest.mark.parametrize("failure", ["bad_reference", "closed_month", "after_item"])
def test_failure_of_any_item_rolls_back_whole_import(session, setup_import, monkeypatch, failure):
    account, categories = setup_import
    doc = document(-100, -200)
    selected = selections(doc, account, categories)
    if failure == "bad_reference":
        selected = (
            selected[0],
            replace(selected[1], draft=replace(selected[1].draft, category_id=str(uuid4()))),
        )
    elif failure == "closed_month":
        selected = (
            selected[0],
            replace(selected[1], draft=replace(selected[1].draft, occurred_on=date(2026, 2, 1))),
        )
        session.periods.close(AccountingMonth(2026, 2))
    else:
        original = SqlImportRepository._insert_item

        def fail(self, *args):
            original(self, *args)
            raise RuntimeError("Синтетический отказ после строки")

        monkeypatch.setattr(SqlImportRepository, "_insert_item", fail)
    before = counts(session)
    with pytest.raises((ValidationError, RuntimeError)):
        session.importing.confirm(doc, account.id, selected)
    assert counts(session) == before and not session.ledger.operations()
    session.check_integrity()


def test_final_day_balance_is_validated_after_all_rows_without_false_intermediate_overflow(
    session, setup_import
):
    account, categories = setup_import
    session.ledger.add(
        OperationDraft(OperationKind.OPENING, date(2026, 1, 1), account.id, Money(MAX_MINOR))
    )
    doc = document(100, -100)
    session.importing.confirm(doc, account.id, selections(doc, account, categories))
    assert session.ledger.balances()[0].amount == Money(MAX_MINOR)
    overflow = document(101)
    before = counts(session)
    with pytest.raises(ValidationError):
        session.importing.confirm(overflow, account.id, selections(overflow, account, categories))
    assert counts(session) == before
    session.check_integrity()


def test_stale_existing_link_rolls_back_import(session, setup_import):
    account, categories = setup_import
    doc = document(-100, -200)
    selected = selections(doc, account, categories)
    operation = session.ledger.add(selected[1].draft)
    session.ledger.replace(
        operation.id, operation.revision_id, replace(operation.draft, note="Поздняя правка")
    )
    selected = (
        selected[0],
        replace(
            selected[1],
            existing_operation_id=operation.id,
            expected_revision_id=operation.revision_id,
        ),
    )
    before = counts(session)
    with pytest.raises(ValidationError, match="изменилась"):
        session.importing.confirm(doc, account.id, selected)
    assert counts(session) == before


def test_two_concurrent_confirmations_do_not_duplicate_source(session, setup_import):
    account, categories = setup_import
    doc = document(-100)
    selected = selections(doc, account, categories)

    def attempt():
        try:
            return session.importing.confirm(doc, account.id, selected)
        except ValidationError:
            return None

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: attempt(), range(2)))
    assert sum(result is not None for result in results) == 1
    assert len(session.ledger.operations()) == 1
    session.check_integrity()


def test_backup_restore_preserves_import_ids_dedup_and_replay(session, setup_import, tmp_path):
    account, categories = setup_import
    doc = document(-100, 200)
    selected, command = selections(doc, account, categories), str(uuid4())
    result = session.importing.confirm(doc, account.id, selected, command_id=command)
    backup = create_backup(session, tmp_path / "import.pfbackup", BACKUP_PASSWORD)
    restored, _ = restore_backup(backup, tmp_path / "restored.pfvault", BACKUP_PASSWORD, PASSWORD)
    try:
        assert restored.replica_id != session.replica_id
        assert restored.importing.confirm(doc, account.id, selected, command_id=command) == result
        assert all(
            line.status == ImportStatus.DUPLICATE
            for line in restored.importing.preview(doc, account.id).lines
        )
        restored.check_integrity()
    finally:
        restored.close()


def test_schema7_is_read_unchanged_and_only_restored_copy_gets_import(tmp_path, monkeypatch):
    with monkeypatch.context() as patch:
        patch.setattr(
            vault_module,
            "initialize_schema",
            lambda e: migration_runner.initialize_schema(e, target="0007"),
        )
        old, _ = vault_module.create_vault(
            tmp_path / "old7.pfvault", "Старая тестовая книга", PASSWORD
        )
    restored = None
    try:
        tag = old.organization.add_tag("Прежний тег")
        root = old.root
        old.close()
        files = [root / "data" / name for name in ("ledger.sqlite3", "key.json")]
        before = [hashlib.sha256(p.read_bytes()).hexdigest() for p in files]
        old = vault_module.open_vault(root, PASSWORD)
        assert old.schema_revision == "0007" and old.importing is None
        backup = create_backup(old, tmp_path / "old7.pfbackup", BACKUP_PASSWORD)
        assert verify_backup(backup, BACKUP_PASSWORD)["schema"] == "0007"
        restored, _ = restore_backup(backup, tmp_path / "new8.pfvault", BACKUP_PASSWORD, PASSWORD)
        assert restored.schema_revision == "0009" and restored.importing is not None
        assert restored.organization.tags() == [tag]
        assert [hashlib.sha256(p.read_bytes()).hexdigest() for p in files] == before
        restored.check_integrity()
    finally:
        old.close()
        if restored:
            restored.close()


def test_sealed_import_is_sql_immutable_and_tampering_is_detected(session, setup_import):
    account, categories = setup_import
    doc = document(-100)
    session.importing.confirm(doc, account.id, selections(doc, account, categories))
    for sql in (
        "UPDATE import_items SET source_hash=source_hash",
        "DELETE FROM import_items",
        "UPDATE import_batches SET sealed=0",
        "DELETE FROM import_batches",
    ):
        with pytest.raises(DatabaseError), session.engine.begin() as c:
            c.execute(text(sql))
    with session.engine.begin() as c:
        c.execute(text("DROP TRIGGER import_item_no_update"))
        c.execute(text("UPDATE import_items SET source_hash=:bad"), {"bad": "0" * 64})
    with pytest.raises(VaultError):
        session.check_integrity()


def test_closed_session_and_other_command_namespace_are_rejected(session, setup_import):
    account, categories = setup_import
    doc = document(-100)
    selected, command = selections(doc, account, categories), str(uuid4())
    session.ledger.add(selected[0].draft, command_id=command)
    with pytest.raises(ValidationError, match="других данных"):
        session.importing.confirm(doc, account.id, selected, command_id=command)
    service = session.importing
    session.close()
    with pytest.raises(VaultError):
        service.preview(doc, account.id)
    with pytest.raises(VaultError):
        service.confirm(doc, account.id, selected)


def test_conflicting_same_id_inside_file_blocks_both_rows(session, setup_import):
    account, categories = setup_import
    doc = document(-100, -200)
    doc = replace(
        doc, rows=(doc.rows[0], replace(doc.rows[1], external_id=doc.rows[0].external_id))
    )
    assert all(
        line.status == ImportStatus.CONFLICT
        for line in session.importing.preview(doc, account.id).lines
    )
    before = counts(session)
    with pytest.raises(ValidationError):
        session.importing.confirm(doc, account.id, selections(doc, account, categories))
    assert counts(session) == before


def test_edited_money_date_keep_original_source_identity_and_audit(session, setup_import):
    account, categories = setup_import
    doc = document(-100)
    selected = selections(doc, account, categories)[0]
    selected = replace(
        selected, draft=replace(selected.draft, amount=Money(150), occurred_on=date(2026, 1, 16))
    )
    result = session.importing.confirm(doc, account.id, (selected,))
    assert result.operations[0].draft.amount == Money(150)
    assert session.ledger.balances()[0].amount == Money(-150)
    assert session.importing.preview(doc, account.id).lines[0].status == ImportStatus.DUPLICATE
    session.check_integrity()


def test_opening_balance_is_not_a_bank_transaction_candidate(session, setup_import):
    account, _ = setup_import
    session.ledger.add(OperationDraft(OperationKind.OPENING, DAY, account.id, Money(100)))
    preview = session.importing.preview(document(100), account.id)
    assert preview.lines[0].status == ImportStatus.NEW and not preview.lines[0].candidates


def test_sql_guard_rejects_missing_account_even_when_adapter_check_is_bypassed(
    session, setup_import, monkeypatch
):
    account, categories = setup_import
    missing = str(uuid4())
    doc = document(-100)
    selected = replace(
        selections(doc, account, categories)[0],
        draft=replace(selections(doc, account, categories)[0].draft, account_id=missing),
    )
    monkeypatch.setattr(SqlImportRepository, "_account", lambda *args, **kwargs: None)
    before = counts(session)
    with pytest.raises(DatabaseError):
        session.importing.confirm(doc, missing, (selected,))
    assert counts(session) == before
