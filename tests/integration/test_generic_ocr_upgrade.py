"""Переход 0011→0012 и общий OCR только на одноразовых зашифрованных книгах."""

import hashlib
from dataclasses import replace
from datetime import date
from uuid import uuid4

import pytest
from alembic import op

from personal_finance.application import vault
from personal_finance.domain.errors import InvalidCredentials, UnsupportedFormat, ValidationError
from personal_finance.domain.imports import digest_json
from personal_finance.domain.ledger import OperationDraft, OperationKind
from personal_finance.domain.models import AccountKind, CategoryKind, Money
from personal_finance.domain.ocr import OcrBox
from personal_finance.domain.ocr_cards import CardCorrections, CashDirection, review_card
from personal_finance.domain.ocr_imports import (
    OcrEvidence,
    OcrImportStatus,
    OcrSelection,
    canonical,
    make_ocr_evidence,
)
from personal_finance.domain.ocr_profiles import UNIVERSAL_PROFILE
from personal_finance.domain.periods import AccountingMonth
from personal_finance.infrastructure.backup.container import (
    create_backup,
    restore_backup,
    verify_backup,
)
from personal_finance.infrastructure.ocr.generic import GenericCardAdapter
from personal_finance.infrastructure.ocr.t_bank import TBankCardAdapter
from personal_finance.infrastructure.ocr.vtb import VtbCardAdapter
from personal_finance.infrastructure.persistence import migration_runner
from personal_finance.infrastructure.persistence.migration_runner import initialize_schema
from tests.conftest import BACKUP_PASSWORD, PASSWORD
from tests.integration.test_generic_ocr_migration import snapshot
from tests.integration.test_ocr_repository import CONFIRMED, MINOR, counts
from tests.ocr_fixtures import document


@pytest.fixture
def schema12(tmp_path, monkeypatch):
    with monkeypatch.context() as patch:
        patch.setattr(
            vault, "initialize_schema", lambda engine: initialize_schema(engine, target="0012")
        )
        session, _ = vault.create_vault(
            tmp_path / "synthetic12.pfvault", "Вымышленная 0012", PASSWORD
        )
    try:
        yield session
    finally:
        session.close()


@pytest.fixture
def schema11(tmp_path, monkeypatch):
    with monkeypatch.context() as patch:
        patch.setattr(
            vault, "initialize_schema", lambda engine: initialize_schema(engine, target="0011")
        )
        session, _ = vault.create_vault(
            tmp_path / "synthetic11.pfvault", "Вымышленная 0011", PASSWORD
        )
    try:
        yield session
    finally:
        session.close()


def chosen(session, profile=UNIVERSAL_PROFILE, *, number=10, scale=1):
    accounts = session.catalog.accounts()
    first = (
        accounts[0]
        if accounts
        else session.catalog.add_account("Вымышленный счёт", AccountKind.CASH)
    )
    category = next(c.id for c in session.catalog.categories() if c.kind == CategoryKind.EXPENSE)
    if profile == "Т-Банк":
        lines = (
            "Т-Банк",
            "Перевод",
            "-4 730,15 RUB",
            "22 октября 2026 в 09:18",
            "Описание: Вымышленная операция",
        )
    else:
        lines = (
            "Детали операции",
            "-4 730,15 RUB",
            "Дата и время",
            "22.10.2026 09:18",
            "Описание: Вымышленная операция",
        )
    doc = document(lines, image_sha256=f"{number:064x}")
    if scale != 1:
        doc = replace(
            doc,
            width=doc.width * scale,
            height=doc.height * scale,
            words=tuple(
                replace(
                    w,
                    box=OcrBox(
                        w.box.left * scale,
                        w.box.top * scale,
                        w.box.width * scale,
                        w.box.height * scale,
                    ),
                )
                for w in doc.words
            ),
        )
    adapter = (
        GenericCardAdapter()
        if profile == UNIVERSAL_PROFILE
        else TBankCardAdapter()
        if profile == "Т-Банк"
        else VtbCardAdapter()
    )
    raw = adapter.parse(doc)
    reviewed = review_card(
        raw,
        CardCorrections(
            "4730,15", "RUB", CashDirection.OUT, "22.10.2026", "09:18", "Вымышленная операция"
        ),
        confirmed_fields=CONFIRMED,
        single_completed_operation=True,
    )
    return OcrSelection(
        make_ocr_evidence(digest_json(["synthetic", doc.image_sha256]), profile, reviewed),
        first.id,
        OperationDraft(
            OperationKind.EXPENSE,
            date(2026, 10, 22),
            first.id,
            Money(MINOR),
            category,
            note=reviewed.description,
        ),
    )


def hashes(root):
    return tuple(
        hashlib.sha256((root / "data" / name).read_bytes()).hexdigest()
        for name in ("ledger.sqlite3", "key.json")
    )


def test_new_explicit_schema12_records_generic_atomically_and_replays(schema12):
    assert schema12.schema_revision == "0012" and schema12.ocr_importing.supports_universal
    value = chosen(schema12)
    command = str(uuid4())
    assert schema12.ocr_importing.preview(value).status == OcrImportStatus.NEW
    result = schema12.ocr_importing.confirm_many((value,), command_id=command)
    assert len(schema12.ledger.operations()) == 1
    assert schema12.ocr_importing.confirm_many((value,), command_id=command) == result
    assert schema12.ocr_importing.preview(value).status == OcrImportStatus.DUPLICATE
    schema12.check_integrity()


def test_old_schema11_refuses_generic_before_any_financial_write(schema11):
    value = chosen(schema11)
    before = counts(schema11)
    assert not schema11.ocr_importing.supports_universal
    for call in (
        lambda: schema11.ocr_importing.preview(value),
        lambda: schema11.ocr_importing.confirm(value),
        lambda: schema11.ocr_importing.preview_many((value,)),
        lambda: schema11.ocr_importing.confirm_many((value,)),
    ):
        with pytest.raises(ValidationError, match="0012"):
            call()
    assert counts(schema11) == before
    schema11.check_integrity()


def test_old_schema_open_is_unchanged_and_restore_preserves_both_profiles_and_receipts(
    schema11, tmp_path, monkeypatch
):
    first = chosen(schema11, "Т-Банк", number=11)
    second = replace(chosen(schema11, "ВТБ", number=12), acknowledged_duplicate=True)
    command = str(uuid4())
    result = schema11.ocr_importing.confirm_many((first, second), command_id=command)
    before = snapshot(schema11.engine)
    financial = (schema11.ledger.operations(), schema11.ledger.balances())
    replica = schema11.replica_id
    backup = create_backup(schema11, tmp_path / "old11.pfbackup", BACKUP_PASSWORD)
    assert verify_backup(backup, BACKUP_PASSWORD)["schema"] == "0011"
    backup_hash = hashlib.sha256(backup.read_bytes()).hexdigest()
    source = schema11.root
    schema11.close()
    original = hashes(source)
    opened = vault.open_vault(source, PASSWORD)
    try:
        assert opened.schema_revision == "0011" and snapshot(opened.engine) == before
        assert not opened.ocr_importing.supports_universal
    finally:
        opened.close()
    assert hashes(source) == original
    with monkeypatch.context() as patch:
        patch.setattr(migration_runner, "SCHEMA_HEAD", "0012")
        restored, _ = restore_backup(
            backup, tmp_path / "restored12.pfvault", BACKUP_PASSWORD, PASSWORD
        )
    try:
        assert restored.schema_revision == "0012" and restored.replica_id != replica
        after = snapshot(restored.engine)
        assert before[:3] == after[:3] and before[4] == after[4]
        assert (restored.ledger.operations(), restored.ledger.balances()) == financial
        assert restored.ocr_importing.confirm_many((first, second), command_id=command) == result
        assert restored.ocr_importing.supports_universal
        third = replace(chosen(restored, number=13), acknowledged_duplicate=True)
        restored.ocr_importing.confirm(third)
        restored.check_integrity()
    finally:
        restored.close()
    assert (
        hashes(source) == original
        and hashlib.sha256(backup.read_bytes()).hexdigest() == backup_hash
    )


@pytest.mark.parametrize(
    "fault",
    [
        'CREATE TABLE "ocr_records_0012"',
        "INSERT INTO ocr_records_0012",
        "DROP TABLE ocr_records",
        "ALTER TABLE ocr_records_0012",
    ],
)
def test_restore_migration_fault_never_publishes_and_preserves_source(
    schema11, tmp_path, monkeypatch, fault
):
    value = chosen(schema11, "Т-Банк")
    schema11.ocr_importing.confirm(value)
    backup = create_backup(schema11, tmp_path / "fault.pfbackup", BACKUP_PASSWORD)
    source = schema11.root
    schema11.close()
    original = hashes(source)
    saved_backup = hashlib.sha256(backup.read_bytes()).hexdigest()
    execute = op.execute

    def fail(statement, *args, **kwargs):
        result = execute(statement, *args, **kwargs)
        if str(statement).startswith(fault):
            raise OSError("Вымышленный отказ миграции 0012")
        return result

    monkeypatch.setattr(migration_runner, "SCHEMA_HEAD", "0012")
    monkeypatch.setattr(op, "execute", fail)
    target = tmp_path / "never-published.pfvault"
    with pytest.raises(OSError):
        restore_backup(backup, target, BACKUP_PASSWORD, PASSWORD)
    assert not target.exists() and hashes(source) == original
    assert hashlib.sha256(backup.read_bytes()).hexdigest() == saved_backup
    reopened = vault.open_vault(source, PASSWORD)
    try:
        reopened.check_integrity()
    finally:
        reopened.close()


def test_generic_other_dpi_is_possible_duplicate_and_can_link_same_financial_fact(schema12):
    first = chosen(schema12, number=20)
    original = schema12.ocr_importing.confirm(first)
    second = chosen(schema12, number=21, scale=2)
    assert schema12.ocr_importing.preview(second).status == OcrImportStatus.POSSIBLE
    linked = replace(
        second,
        existing_operation_id=original.operation.id,
        expected_revision_id=original.operation.revision_id,
    )
    result = schema12.ocr_importing.confirm(linked)
    assert result.linked and len(schema12.ledger.operations()) == 1
    schema12.check_integrity()


def test_same_image_cannot_be_reinterpreted_with_another_profile(schema12):
    first = chosen(schema12, "Т-Банк", number=30)
    schema12.ocr_importing.confirm(first)
    second = chosen(schema12, number=30)
    assert schema12.ocr_importing.preview(second).status == OcrImportStatus.CONFLICT
    before = counts(schema12)
    with pytest.raises(ValidationError):
        schema12.ocr_importing.confirm(second)
    assert counts(schema12) == before


@pytest.mark.parametrize(
    "profile,adapter",
    [
        ("Любой банк", "vtb-card-v1"),
        ("Т-Банк", "generic-card-v1"),
        ("Неизвестный банк", "generic-card-v1"),
    ],
)
def test_profile_adapter_mismatch_is_refused(schema12, profile, adapter):
    value = chosen(schema12)
    data = value.evidence.data
    data["bank"], data["adapter"] = profile, adapter
    with pytest.raises(ValidationError):
        OcrEvidence(canonical(data))


def test_previous_supported_schema_set_refuses_new_book_without_changes(schema12, monkeypatch):
    root = schema12.root
    schema12.close()
    before = hashes(root)
    monkeypatch.setattr(
        migration_runner,
        "SUPPORTED_SCHEMAS",
        tuple(v for v in migration_runner.SUPPORTED_SCHEMAS if v != "0012"),
    )
    with pytest.raises(UnsupportedFormat):
        vault.open_vault(root, PASSWORD)
    assert hashes(root) == before


NEW_PASSWORD = "Вымышленная новая фраза для восстановления 0012"


def test_generic_backup_restores_new_password_recovery_replica_and_command(
    schema12, tmp_path, monkeypatch
):
    value = chosen(schema12, number=41)
    command = str(uuid4())
    result = schema12.ocr_importing.confirm_many((value,), command_id=command)
    replica = schema12.replica_id
    original_key_hash = hashes(schema12.root)[1]
    backup = create_backup(schema12, tmp_path / "generic12.pfbackup", BACKUP_PASSWORD)
    assert verify_backup(backup, BACKUP_PASSWORD)["schema"] == "0012"
    with monkeypatch.context() as patch:
        patch.setattr(migration_runner, "SCHEMA_HEAD", "0012")
        restored, recovery = restore_backup(
            backup, tmp_path / "generic-restored.pfvault", BACKUP_PASSWORD, NEW_PASSWORD
        )
    root = restored.root
    try:
        assert restored.replica_id != replica
        assert restored.ocr_importing.confirm_many((value,), command_id=command) == result
        assert hashes(root)[1] != original_key_hash
        restored.check_integrity()
    finally:
        restored.close()
    with pytest.raises(InvalidCredentials):
        vault.open_vault(root, PASSWORD)
    by_password = vault.open_vault(root, NEW_PASSWORD)
    try:
        assert by_password.ocr_importing.preview(value).status == OcrImportStatus.DUPLICATE
    finally:
        by_password.close()
    by_recovery = vault.open_vault(root, recovery, recovery=True)
    try:
        by_recovery.check_integrity()
        assert len(by_recovery.ledger.operations()) == 1
    finally:
        by_recovery.close()


def test_generic_failure_after_second_row_rolls_back_finances_audit_and_receipts(
    schema12, monkeypatch
):
    first = chosen(schema12, number=51)
    second = replace(chosen(schema12, number=52), acknowledged_duplicate=True)
    repository = schema12.ocr_importing._repository
    original = repository._confirm_in_transaction
    calls = []

    def fail(connection, value, command):
        result = original(connection, value, command)
        calls.append(value)
        if len(calls) == 2:
            raise OSError("Вымышленный отказ общего пакета")
        return result

    before = counts(schema12), snapshot(schema12.engine)
    monkeypatch.setattr(repository, "_confirm_in_transaction", fail)
    with pytest.raises(OSError):
        schema12.ocr_importing.confirm_many((first, second))
    assert (counts(schema12), snapshot(schema12.engine)) == before
    schema12.check_integrity()


def test_generic_cannot_bypass_closed_period(schema12):
    value = chosen(schema12, number=61)
    schema12.periods.close(AccountingMonth(2026, 10))
    before = counts(schema12), snapshot(schema12.engine)
    with pytest.raises(ValidationError):
        schema12.ocr_importing.confirm(value)
    assert (counts(schema12), snapshot(schema12.engine)) == before
    schema12.check_integrity()
