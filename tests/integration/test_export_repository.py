import json
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import date
from threading import Event
from uuid import uuid4

import pytest
from sqlalchemy import text

from personal_finance.application import vault as vault_module
from personal_finance.domain.errors import FinanceError, ValidationError, VaultError
from personal_finance.domain.exporting import ExportFormat, ExportSettings, serialize_journal_export
from personal_finance.domain.ledger import OperationDraft, OperationKind, OperationPart
from personal_finance.domain.models import AccountKind, CategoryKind, Money
from personal_finance.domain.periods import AccountingMonth
from personal_finance.domain.query import OperationQuery
from personal_finance.infrastructure.backup.container import create_backup, restore_backup
from personal_finance.infrastructure.exporting import files
from personal_finance.infrastructure.persistence import migration_runner
from personal_finance.infrastructure.persistence.export_repository import SqlExportRepository
from tests.conftest import BACKUP_PASSWORD, PASSWORD

DAY = date(2026, 1, 15)
TABLES = (
    "entities",
    "entity_revisions",
    "revision_parents",
    "revision_heads",
    "change_groups",
    "change_log",
    "command_receipts",
    "postings",
    "operation_tags",
    "import_batches",
    "import_items",
)


def state(session):
    with session.engine.connect() as c:
        return [
            c.execute(text("SELECT count(*) FROM " + name)).scalar_one() for name in TABLES
        ] + list(c.execute(text("SELECT sequence FROM replicas ORDER BY id")).scalars())


@pytest.fixture
def export_data(session):
    bank = session.catalog.institutions()[0]
    account = session.catalog.add_account("СЧЁТ_ЭКСПОРТА", AccountKind.DEBIT, bank.id)
    other = session.catalog.add_account("Получатель", AccountKind.CASH)
    categories = session.catalog.categories()
    expenses = [c for c in categories if c.kind == CategoryKind.EXPENSE]
    income = next(c.id for c in categories if c.kind == CategoryKind.INCOME)
    child = session.catalog.add_category(
        "Подкатегория экспорта", CategoryKind.EXPENSE, expenses[0].id
    )
    tag = session.organization.add_tag("ТЕГ_ЭКСПОРТА")
    session.ledger.add(
        OperationDraft(OperationKind.OPENING, date(2026, 1, 1), account.id, Money(100000))
    )
    spending = session.ledger.add(
        OperationDraft(
            OperationKind.EXPENSE,
            DAY,
            account.id,
            Money(2500),
            parts=(
                OperationPart(child.id, Money(1000)),
                OperationPart(expenses[1].id, Money(1500)),
            ),
            tag_ids=(tag.id,),
            note="ПРИМЕЧАНИЕ_ЭКСПОРТА",
        )
    )
    transfer = session.ledger.add(
        OperationDraft(
            OperationKind.TRANSFER,
            date(2026, 1, 16),
            account.id,
            Money(20000),
            destination_account_id=other.id,
            fee=Money(100),
            fee_category_id=expenses[1].id,
        )
    )
    session.ledger.add(
        OperationDraft(
            OperationKind.REFUND,
            date(2026, 1, 17),
            account.id,
            Money(500),
            child.id,
            refund_of=spending.id,
            refund_of_revision_id=spending.revision_id,
        )
    )
    session.ledger.add(
        OperationDraft(OperationKind.INCOME, date(2026, 1, 18), account.id, Money(10000), income)
    )
    deleted = session.ledger.add(
        OperationDraft(
            OperationKind.EXPENSE, date(2026, 1, 19), account.id, Money(100), expenses[0].id
        )
    )
    session.ledger.delete(deleted.id, deleted.revision_id)
    return account, other, bank, child, expenses[0], tag, spending, transfer


def test_export_reads_consistent_names_parts_and_refs_without_any_book_writes(session, export_data):
    account, _, bank, child, parent, tag, spending, transfer = export_data
    before, balances = state(session), session.ledger.balances()
    preview = session.exporting.preview(ExportSettings(include_tags=True))
    rows = {row["operation_id"]: row for row in preview.rows()}
    assert len(rows) == 5 and preview.source_book_id == session.book_id
    assert (
        rows[spending.id]["account_name"] == account.name
        and rows[spending.id]["bank_name"] == bank.name
    )
    assert rows[spending.id]["parts"][0]["category_name"] == parent.name + " / " + child.name
    assert rows[spending.id]["tag_names"] == [tag.name]
    assert rows[transfer.id]["amount_minor"] == "20000" and rows[transfer.id]["fee_minor"] == "100"
    assert state(session) == before and session.ledger.balances() == balances
    session.check_integrity()


def test_sensitive_optional_fields_and_unselected_names_are_excluded(session, export_data):
    account, _, _, _, _, tag, spending, _ = export_data
    session.catalog.add_account("НЕ_ВЫБРАННЫЙ_СЧЁТ", AccountKind.CASH)
    settings = ExportSettings(OperationQuery(text=spending.draft.note), include_names=False)
    preview = session.exporting.preview(settings)
    assert preview.operations == (spending,)
    for format in ExportFormat:
        raw = serialize_journal_export(preview, format)
        for marker in (
            account.name,
            tag.name,
            spending.draft.note,
            "НЕ_ВЫБРАННЫЙ_СЧЁТ",
            session.book_id,
            PASSWORD,
        ):
            assert marker.encode() not in raw


def test_filters_include_whole_transfer_fee_parts_and_explicit_deleted(session, export_data):
    account, other, bank, child, _, _, spending, transfer = export_data
    settings = ExportSettings(OperationQuery(account_ids=(other.id,)))
    assert session.exporting.preview(settings).operations == (transfer,)
    settings = ExportSettings(OperationQuery(account_ids=(other.id,), institution_ids=(bank.id,)))
    assert not session.exporting.preview(settings).operations
    settings = ExportSettings(OperationQuery(category_ids=(child.id,)))
    assert {op.draft.kind for op in session.exporting.preview(settings).operations} == {
        OperationKind.EXPENSE,
        OperationKind.REFUND,
    }
    settings = ExportSettings(OperationQuery(start=DAY, end=DAY, kinds=(OperationKind.EXPENSE,)))
    assert session.exporting.preview(settings).operations == (spending,)
    assert (
        len(
            session.exporting.preview(
                ExportSettings(OperationQuery(include_deleted=True))
            ).operations
        )
        == 6
    )


def test_save_uses_reviewed_snapshot_even_after_edits_and_tag_rename(
    session, export_data, tmp_path
):
    _, _, _, _, _, tag, spending, _ = export_data
    preview = session.exporting.preview(ExportSettings(include_notes=True, include_tags=True))
    session.organization.rename_tag(tag.id, tag.revision_id, "Позднее имя")
    session.ledger.replace(
        spending.id, spending.revision_id, replace(spending.draft, note="Позднее примечание")
    )
    before = state(session)
    path = session.exporting.save(tmp_path / "reviewed.json", preview, ExportFormat.JSON)
    rows = {row["operation_id"]: row for row in json.loads(path.read_bytes())["operations"]}
    assert rows[spending.id]["note"] == spending.draft.note and rows[spending.id]["tag_names"] == [
        tag.name
    ]
    assert state(session) == before


def test_export_stays_available_in_closed_period_and_archived_tag_keeps_name(
    session, export_data, tmp_path
):
    _, _, _, _, _, tag, spending, _ = export_data
    session.organization.archive_tag(tag.id, tag.revision_id)
    session.periods.close(AccountingMonth(2026, 1))
    before = state(session)
    preview = session.exporting.preview(
        ExportSettings(OperationQuery(tag_ids=(tag.id,)), include_tags=True)
    )
    assert preview.operations == (spending,) and preview.rows()[0]["tag_names"] == [tag.name]
    session.exporting.save(tmp_path / "closed.csv", preview, ExportFormat.CSV)
    assert state(session) == before


def test_concurrent_metadata_write_waits_for_full_export_snapshot(
    session, export_data, monkeypatch
):
    _, _, _, _, _, tag, _, _ = export_data
    ready, release, attempted = Event(), Event(), Event()
    read = SqlExportRepository._read_labels

    def pause(self, *args):
        ready.set()
        assert release.wait(10)
        return read(self, *args)

    def rename():
        attempted.set()
        return session.organization.rename_tag(tag.id, tag.revision_id, "Имя после снимка")

    monkeypatch.setattr(SqlExportRepository, "_read_labels", pause)
    with ThreadPoolExecutor(max_workers=2) as pool:
        reading = pool.submit(session.exporting.preview, ExportSettings(include_tags=True))
        try:
            assert ready.wait(10)
            writing = pool.submit(rename)
            assert attempted.wait(10) and not writing.done()
        finally:
            release.set()
        preview = reading.result(timeout=15)
        writing.result(timeout=15)
    assert dict(preview.labels.tags)[tag.id] == tag.name
    assert session.organization.tags()[0].name == "Имя после снимка"


def test_wrong_book_preview_closed_session_and_existing_target_are_rejected(
    session, export_data, tmp_path
):
    preview = session.exporting.preview(ExportSettings())
    target = tmp_path / "guard.json"
    with pytest.raises(ValidationError, match="другой книге"):
        session.exporting.save(
            target, replace(preview, source_book_id=str(uuid4())), ExportFormat.JSON
        )
    assert not target.exists()
    target.write_bytes(b"keep existing")
    with pytest.raises(ValidationError, match="существует"):
        session.exporting.save(target, preview, ExportFormat.JSON)
    assert target.read_bytes() == b"keep existing"
    service = session.exporting
    session.close()
    with pytest.raises(VaultError):
        service.preview(ExportSettings())
    with pytest.raises(VaultError):
        service.save(tmp_path / "closed.json", preview, ExportFormat.JSON)
    assert not (tmp_path / "closed.json").exists()


def test_write_failure_does_not_change_book_or_leave_export_file(
    session, export_data, tmp_path, monkeypatch
):
    preview = session.exporting.preview(ExportSettings())
    before = state(session)

    def fail(*args):
        raise OSError("Синтетический отказ публикации")

    monkeypatch.setattr(files, "_publish", fail)
    with pytest.raises(FinanceError):
        session.exporting.save(tmp_path / "failed.json", preview, ExportFormat.JSON)
    assert not (tmp_path / "failed.json").exists()
    assert not list(tmp_path.glob(".crown-export-*"))
    assert state(session) == before


def test_restore_keeps_same_exported_financial_data(session, export_data, tmp_path):
    expected = session.exporting.preview(ExportSettings(include_notes=True, include_tags=True))
    backup = create_backup(session, tmp_path / "export.pfbackup", BACKUP_PASSWORD)
    restored, _ = restore_backup(backup, tmp_path / "restored.pfvault", BACKUP_PASSWORD, PASSWORD)
    try:
        actual = restored.exporting.preview(expected.settings)
        assert serialize_journal_export(actual, ExportFormat.JSON) == serialize_journal_export(
            expected, ExportFormat.JSON
        )
        assert restored.replica_id != session.replica_id
    finally:
        restored.close()


def test_schema3_supports_export_without_migrating_original_book(tmp_path, monkeypatch):
    with monkeypatch.context() as patch:
        patch.setattr(
            vault_module,
            "initialize_schema",
            lambda e: migration_runner.initialize_schema(e, target="0003"),
        )
        old, _ = vault_module.create_vault(
            tmp_path / "old3.pfvault", "Старая тестовая книга", PASSWORD
        )
    try:
        account = old.catalog.add_account("Прежний счёт", AccountKind.CASH)
        category = next(c.id for c in old.catalog.categories() if c.kind == CategoryKind.INCOME)
        operation = old.ledger.add(
            OperationDraft(OperationKind.INCOME, DAY, account.id, Money(123), category)
        )
        preview = old.exporting.preview(ExportSettings(include_tags=True))
        assert preview.operations == (operation,) and preview.rows()[0]["tag_ids"] == []
        old.exporting.save(tmp_path / "old3.csv", preview, ExportFormat.CSV)
        assert old.schema_revision == "0003"
        old.check_integrity()
    finally:
        old.close()
