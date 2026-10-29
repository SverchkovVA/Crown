"""Синтетические проверки атомарного OCR-аудита и финансового журнала."""

from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import date
from uuid import uuid4

import pytest
from sqlalchemy import event, text
from sqlalchemy.exc import DatabaseError

from personal_finance.application.vault import create_vault
from personal_finance.domain.errors import ValidationError, VaultError
from personal_finance.domain.imports import digest_json
from personal_finance.domain.ledger import OperationDraft, OperationKind
from personal_finance.domain.models import AccountKind, CategoryKind, Money
from personal_finance.domain.ocr_cards import CardCorrections, CashDirection, review_card
from personal_finance.domain.ocr_imports import (
    OcrEvidence,
    OcrImportStatus,
    OcrSelection,
    canonical,
    make_ocr_evidence,
)
from personal_finance.domain.periods import AccountingMonth
from personal_finance.infrastructure.backup.container import (
    create_backup,
    restore_backup,
    verify_backup,
)
from personal_finance.infrastructure.ocr.t_bank_history import TBankHistoryAdapter
from tests.conftest import BACKUP_PASSWORD, PASSWORD
from tests.ocr_fixtures import card, history_document

DAY = date(2026, 10, 22)
MINOR = 473015
CONFIRMED = frozenset({"amount", "currency", "direction", "occurred_on"})
LINES = (
    "Т-Банк",
    "Перевод",
    "-4 730,15 RUB",
    "22 октября 2026 в 09:18",
    "Описание: Вымышленная операция",
)
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
    "ocr_records",
)


@pytest.fixture
def accounts(session):
    source = session.catalog.add_account("Синтетический OCR-счёт", AccountKind.CASH)
    destination = session.catalog.add_account("Синтетический второй счёт", AccountKind.CASH)
    categories = {
        kind: next(c.id for c in session.catalog.categories() if c.kind == kind)
        for kind in CategoryKind
    }
    return source, destination, categories


def checked(source=None, *, direction=CashDirection.OUT):
    return review_card(
        source or card(LINES),
        CardCorrections("4730,15", "RUB", direction, "22.10.2026", "09:18", "Вымышленная операция"),
        confirmed_fields=CONFIRMED,
        single_completed_operation=True,
    )


def selection(accounts, *, source=None, direction=CashDirection.OUT, account=None, draft=None):
    first, _, categories = accounts
    account = account or first.id
    kind = OperationKind.EXPENSE if direction == CashDirection.OUT else OperationKind.INCOME
    category = categories[
        CategoryKind.EXPENSE if direction == CashDirection.OUT else CategoryKind.INCOME
    ]
    draft = draft or OperationDraft(kind, DAY, account, Money(MINOR), category)
    return OcrSelection(
        make_ocr_evidence(
            digest_json(
                [
                    "synthetic-file",
                    checked(source, direction=direction).source.document.image_sha256,
                ]
            ),
            "Т-Банк",
            checked(source, direction=direction),
        ),
        account,
        draft,
    )


def counts(session):
    with session.engine.connect() as connection:
        return {
            name: connection.execute(text("SELECT count(*) FROM " + name)).scalar_one()
            for name in TABLES
        }


def test_ocr_atomically_saves_operation_and_provenance_with_repeat_guard(session, accounts):
    value = selection(accounts)
    service = session.ocr_importing
    before = len(session.ledger.operations())
    assert service.preview(value).status == OcrImportStatus.NEW
    command = str(uuid4())
    result = service.confirm(value, command_id=command)
    assert result.operation.draft == value.draft and not result.linked
    assert service.confirm(value, command_id=command) == result
    assert len(session.ledger.operations()) == before + 1
    assert service.preview(value).status == OcrImportStatus.DUPLICATE
    before = counts(session)
    with pytest.raises(ValidationError):
        service.confirm(value)
    assert counts(session) == before
    session.check_integrity()


def test_same_command_returns_original_revision_after_edit_and_delete(session, accounts):
    value = selection(accounts)
    command = str(uuid4())
    result = session.ocr_importing.confirm(value, command_id=command)
    changed = session.ledger.replace(
        result.operation.id,
        result.operation.revision_id,
        replace(value.draft, note="Последующая правка"),
    )
    session.ledger.delete(changed.id, changed.revision_id)
    before = counts(session)
    assert session.ocr_importing.confirm(value, command_id=command) == result
    assert counts(session) == before and session.ledger.operations() == []
    session.check_integrity()


def test_command_payload_and_other_command_namespace_conflict(session, accounts):
    value = selection(accounts)
    command = str(uuid4())
    session.ocr_importing.confirm(value, command_id=command)
    with pytest.raises(ValidationError):
        session.ocr_importing.confirm(
            replace(value, acknowledged_duplicate=True), command_id=command
        )
    other = str(uuid4())
    session.ledger.add(replace(value.draft, note="Ручная команда"), command_id=other)
    before = counts(session)
    with pytest.raises(ValidationError):
        session.ocr_importing.confirm(value, command_id=other)
    assert counts(session) == before


def test_changing_observed_account_does_not_bypass_source_dedup(session, accounts):
    value = selection(accounts)
    session.ocr_importing.confirm(value)
    _, second, _ = accounts
    altered = replace(value, account_id=second.id, draft=replace(value.draft, account_id=second.id))
    assert session.ocr_importing.preview(altered).status == OcrImportStatus.CONFLICT
    with pytest.raises(ValidationError):
        session.ocr_importing.confirm(altered)
    assert len(session.ledger.operations()) == 1


def test_same_image_changed_ocr_context_is_conflict(session, accounts):
    value = selection(accounts)
    session.ocr_importing.confirm(value)
    original = card(LINES)
    newer = replace(
        original,
        document=replace(
            original.document, engine=replace(original.document.engine, version="synthetic-2")
        ),
    )
    altered = selection(accounts, source=newer)
    assert altered.evidence.source_key == value.evidence.source_key
    assert session.ocr_importing.preview(altered).status == OcrImportStatus.CONFLICT


def test_overlapping_amount_regions_cannot_create_another_source_key(session, accounts):
    value = selection(accounts)
    session.ocr_importing.confirm(value)
    data = value.evidence.data
    extra = data["fields"]["description"]["words"][0]
    data["fields"]["amount"]["words"].append(extra)
    altered = replace(value, evidence=OcrEvidence(canonical(data)))
    assert altered.evidence.source_key != value.evidence.source_key
    assert session.ocr_importing.preview(altered).status == OcrImportStatus.CONFLICT
    with pytest.raises(ValidationError):
        session.ocr_importing.confirm(altered)


def test_metadata_only_copy_is_duplicate_not_a_new_movement(session, accounts):
    value = selection(accounts)
    command = str(uuid4())
    session.ocr_importing.confirm(value, command_id=command)
    data = value.evidence.data
    data["file_sha256"] = "4" * 64
    copied = replace(value, evidence=OcrEvidence(canonical(data)))
    assert session.ocr_importing.preview(copied).status == OcrImportStatus.DUPLICATE
    with pytest.raises(ValidationError):
        session.ocr_importing.confirm(copied, command_id=command)
    with pytest.raises(ValidationError):
        session.ocr_importing.confirm(copied)
    assert len(session.ledger.operations()) == 1


def test_other_image_with_same_movement_requires_acknowledgment_or_link(session, accounts):
    first = selection(accounts)
    session.ocr_importing.confirm(first)
    second = selection(accounts, source=card(LINES, image_sha256="f" * 64))
    preview = session.ocr_importing.preview(second)
    assert preview.status == OcrImportStatus.POSSIBLE and len(preview.candidates) == 1
    before = counts(session)
    with pytest.raises(ValidationError):
        session.ocr_importing.confirm(second)
    assert counts(session) == before
    result = session.ocr_importing.confirm(replace(second, acknowledged_duplicate=True))
    assert result.operation.id != preview.candidates[0].id and not result.linked
    assert len(session.ledger.operations()) == 2
    session.check_integrity()


def test_two_equal_rows_one_image_are_distinct_and_not_automatically_merged(session, accounts):
    doc = history_document(header="22 октября 2026", values=("-4 730,15 RUB", "-4 730,15 RUB"))
    cards = TBankHistoryAdapter().parse(doc).cards
    first, second = (selection(accounts, source=source) for source in cards)
    assert first.evidence.source_key != second.evidence.source_key
    session.ocr_importing.confirm(first)
    assert session.ocr_importing.preview(second).status == OcrImportStatus.POSSIBLE
    with pytest.raises(ValidationError):
        session.ocr_importing.confirm(second)
    session.ocr_importing.confirm(replace(second, acknowledged_duplicate=True))
    assert len(session.ledger.operations()) == 2
    session.check_integrity()


def test_link_existing_manual_operation_adds_only_provenance(session, accounts):
    value = selection(accounts)
    manual = session.ledger.add(value.draft)
    before_ops = (
        session.ledger.operations(),
        session.ledger.balances(),
        session.ledger.history(manual.id),
    )
    linked = replace(
        value, existing_operation_id=manual.id, expected_revision_id=manual.revision_id
    )
    result = session.ocr_importing.confirm(linked)
    assert result.linked and result.operation == manual
    assert (
        session.ledger.operations(),
        session.ledger.balances(),
        session.ledger.history(manual.id),
    ) == before_ops
    session.check_integrity()


def test_stale_link_is_rechecked_inside_confirmation(session, accounts):
    value = selection(accounts)
    manual = session.ledger.add(value.draft)
    selected = replace(
        value, existing_operation_id=manual.id, expected_revision_id=manual.revision_id
    )
    session.ledger.replace(
        manual.id, manual.revision_id, replace(manual.draft, note="Изменено после просмотра")
    )
    before = counts(session)
    with pytest.raises(ValidationError):
        session.ocr_importing.confirm(selected)
    assert counts(session) == before


def test_both_observed_sides_of_own_transfer_link_to_one_operation(session, accounts):
    first, second, _ = accounts
    draft = OperationDraft(
        OperationKind.TRANSFER, DAY, first.id, Money(MINOR), destination_account_id=second.id
    )
    document = history_document(header="22 октября 2026", values=("-4 730,15 RUB", "+4 730,15 RUB"))
    cards = TBankHistoryAdapter().parse(document).cards
    outbound = selection(accounts, source=cards[0], account=first.id, draft=draft)
    first_result = session.ocr_importing.confirm(outbound)
    inbound = selection(
        accounts, source=cards[1], direction=CashDirection.IN, account=second.id, draft=draft
    )
    inbound = replace(
        inbound,
        existing_operation_id=first_result.operation.id,
        expected_revision_id=first_result.operation.revision_id,
    )
    assert session.ocr_importing.confirm(inbound).linked
    assert len(session.ledger.operations()) == 1
    with session.engine.connect() as connection:
        assert connection.execute(text("SELECT count(*) FROM ocr_records")).scalar_one() == 2
    session.check_integrity()


def test_fee_transfer_exact_gross_movement_is_saved_by_common_ledger(session, accounts):
    first, second, categories = accounts
    draft = OperationDraft(
        OperationKind.TRANSFER,
        DAY,
        first.id,
        Money(MINOR - 15),
        destination_account_id=second.id,
        fee=Money(15),
        fee_category_id=categories[CategoryKind.EXPENSE],
    )
    value = selection(accounts, draft=draft)
    result = session.ocr_importing.confirm(value)
    assert result.operation.draft == draft
    balances = {b.account_id: b.amount.minor for b in session.ledger.balances()}
    assert balances[first.id] == -MINOR and balances[second.id] == MINOR - 15
    session.check_integrity()


def test_closed_period_blocks_new_operation_but_permits_link_without_money_change(
    session, accounts
):
    value = selection(accounts)
    manual = session.ledger.add(value.draft)
    session.periods.close(AccountingMonth(DAY.year, DAY.month))
    before = counts(session)
    with pytest.raises(ValidationError):
        session.ocr_importing.confirm(replace(value, acknowledged_duplicate=True))
    assert counts(session) == before
    linked = replace(
        value, existing_operation_id=manual.id, expected_revision_id=manual.revision_id
    )
    assert session.ocr_importing.confirm(linked).linked
    assert session.ledger.operations() == [manual]
    session.check_integrity()


def test_invalid_refund_reference_is_rejected_without_partial_audit(session, accounts):
    account, _, categories = accounts
    draft = OperationDraft(
        OperationKind.REFUND,
        DAY,
        account.id,
        Money(MINOR),
        categories[CategoryKind.EXPENSE],
        refund_of=str(uuid4()),
        refund_of_revision_id=str(uuid4()),
    )
    value = selection(accounts, direction=CashDirection.IN, draft=draft)
    before = counts(session)
    with pytest.raises(ValidationError):
        session.ocr_importing.confirm(value)
    assert counts(session) == before


def test_other_book_account_and_closed_session_are_rejected(session, accounts, tmp_path):
    other, _ = create_vault(tmp_path / "foreign.pfvault", "Синтетическая чужая книга", PASSWORD)
    try:
        foreign = other.catalog.add_account("Чужой счёт", AccountKind.CASH)
        value = selection(accounts, account=foreign.id)
        before = counts(session)
        with pytest.raises(ValidationError):
            session.ocr_importing.confirm(value)
        assert counts(session) == before
    finally:
        other.close()
    value = selection(accounts)
    service = session.ocr_importing
    session.close()
    with pytest.raises(VaultError):
        service.preview(value)
    with pytest.raises(VaultError):
        service.confirm(value)


def test_failure_after_financial_write_rolls_back_every_table(session, accounts):
    value = selection(accounts)
    before = counts(session)

    def fail(connection, cursor, statement, parameters, context, executemany):
        if statement.lstrip().upper().startswith("INSERT INTO OCR_RECORDS"):
            raise RuntimeError("Синтетический отказ перед аудитом")

    event.listen(session.engine, "before_cursor_execute", fail)
    try:
        with pytest.raises(RuntimeError, match="Синтетический отказ"):
            session.ocr_importing.confirm(value)
    finally:
        event.remove(session.engine, "before_cursor_execute", fail)
    assert counts(session) == before and session.ledger.operations() == []
    session.check_integrity()


def test_two_concurrent_confirmations_create_one_financial_operation(session, accounts):
    value = selection(accounts)

    def attempt():
        try:
            return session.ocr_importing.confirm(value)
        except ValidationError:
            return None

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: attempt(), range(2)))
    assert sum(result is not None for result in results) == 1
    assert len(session.ledger.operations()) == 1
    session.check_integrity()


def test_ocr_records_are_sql_immutable(session, accounts):
    session.ocr_importing.confirm(selection(accounts))
    for sql in ("UPDATE ocr_records SET evidence_hash=evidence_hash", "DELETE FROM ocr_records"):
        with pytest.raises(DatabaseError), session.engine.begin() as connection:
            connection.execute(text(sql))
    session.check_integrity()


@pytest.mark.parametrize("target", ["evidence_hash", "evidence_json", "receipt"])
def test_integrity_detects_tampering_after_guard_is_removed(session, accounts, target):
    value = selection(accounts)
    command = str(uuid4())
    session.ocr_importing.confirm(value, command_id=command)
    with session.engine.begin() as connection:
        if target == "receipt":
            names = (
                connection.execute(
                    text(
                        "SELECT name FROM sqlite_master WHERE type='trigger' "
                        "AND tbl_name='command_receipts'"
                    )
                )
                .scalars()
                .all()
            )
            for name in names:
                connection.exec_driver_sql('DROP TRIGGER "' + name.replace('"', '""') + '"')
            connection.execute(
                text("UPDATE command_receipts SET command_hash=:bad WHERE id=:id"),
                {"bad": "0" * 64, "id": command},
            )
        else:
            connection.execute(text("DROP TRIGGER ocr_no_update"))
            if target == "evidence_hash":
                connection.execute(
                    text("UPDATE ocr_records SET evidence_hash=:bad"), {"bad": "0" * 64}
                )
            else:
                data = value.evidence.data
                data["reviewed"]["description"] = "Подменённое синтетическое описание"
                data["changed_fields"].append("description")
                connection.execute(
                    text("UPDATE ocr_records SET evidence_json=:payload"),
                    {"payload": canonical(data)},
                )
    with pytest.raises(VaultError):
        session.check_integrity()


def test_backup_restore_keeps_audit_dedup_and_receipts_under_new_replica(
    session, accounts, tmp_path
):
    value = selection(accounts)
    command = str(uuid4())
    result = session.ocr_importing.confirm(value, command_id=command)
    backup = create_backup(session, tmp_path / "ocr.pfbackup", BACKUP_PASSWORD)
    assert verify_backup(backup, BACKUP_PASSWORD)["schema"] == "0013"
    restored, _ = restore_backup(
        backup, tmp_path / "restored-ocr.pfvault", BACKUP_PASSWORD, PASSWORD
    )
    try:
        assert restored.replica_id != session.replica_id
        assert restored.ocr_importing.confirm(value, command_id=command) == result
        assert restored.ocr_importing.preview(value).status == OcrImportStatus.DUPLICATE
        with pytest.raises(ValidationError):
            restored.ocr_importing.confirm(value)
        restored.check_integrity()
    finally:
        restored.close()


@pytest.mark.parametrize(
    "alteration", ["account", "image_metadata", "movement", "hash_shape", "context", "overlap"]
)
def test_sql_rejects_invalid_new_audit_record_even_with_valid_receipt(
    session, accounts, alteration
):
    value = selection(accounts)
    recorded = session.ocr_importing.confirm(value)
    with session.engine.connect() as connection:
        row = dict(
            connection.execute(
                text("SELECT * FROM ocr_records WHERE id=:id"), {"id": recorded.audit_id}
            )
            .mappings()
            .one()
        )
    data = value.evidence.data
    data["image_sha256"] = "b" * 64
    if alteration in ("context", "overlap"):
        data["image_sha256"] = value.evidence.data["image_sha256"]
        data["fields"]["amount"]["words"].append(data["fields"]["description"]["words"][0])
        if alteration == "context":
            data["document_sha256"] = "c" * 64
    proof = OcrEvidence(canonical(data))
    linked = replace(
        value,
        evidence=proof,
        existing_operation_id=recorded.operation.id,
        expected_revision_id=recorded.operation.revision_id,
    )
    command = str(uuid4())
    digest = session.ocr_importing._repository._request(linked)
    row.update(
        id=str(uuid4()),
        image_sha256=data["image_sha256"],
        document_sha256=data["document_sha256"],
        source_key=proof.source_key,
        evidence_hash=proof.content_hash,
        evidence_json=proof.payload,
        command_id=command,
        command_hash=digest,
        linked=1,
    )
    if alteration == "account":
        row["account_id"] = accounts[1].id
    elif alteration == "image_metadata":
        row["image_sha256"] = "d" * 64
    elif alteration == "movement":
        changed = proof.data
        changed["reviewed"]["minor"] += 1
        row["evidence_json"] = canonical(changed)
    elif alteration == "hash_shape":
        row["evidence_hash"] = "bad"
    before = counts(session)
    expected = "context conflict" if alteration in ("context", "overlap") else None
    with pytest.raises(DatabaseError, match=expected), session.engine.begin() as connection:
        connection.execute(
            text(
                "INSERT INTO command_receipts(id,command_hash,entity_id,revision_id) "
                "VALUES (:id,:hash,:entity,:revision)"
            ),
            {
                "id": command,
                "hash": digest,
                "entity": recorded.operation.id,
                "revision": recorded.operation.revision_id,
            },
        )
        columns = ",".join(row)
        bindings = ",".join(":" + name for name in row)
        connection.execute(
            text("INSERT INTO ocr_records (" + columns + ") VALUES (" + bindings + ")"), row
        )
    assert counts(session) == before
    session.check_integrity()
