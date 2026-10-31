from dataclasses import replace
from uuid import uuid4

import pytest
from sqlalchemy import text

from personal_finance.domain.errors import ValidationError
from personal_finance.domain.models import AccountKind, CategoryKind
from personal_finance.domain.ocr_imports import OcrEvidence, OcrImportStatus, canonical
from personal_finance.domain.ocr_settings import OcrPreferences, TransferParty
from personal_finance.infrastructure.backup.container import create_backup, restore_backup
from tests.conftest import BACKUP_PASSWORD, PASSWORD
from tests.integration.test_ocr_repository import counts, selection


@pytest.fixture
def chosen(session):
    first = session.catalog.add_account("Вымышленный первый счёт", AccountKind.CASH)
    second = session.catalog.add_account("Вымышленный второй счёт", AccountKind.CASH)
    categories = {
        kind: next(c.id for c in session.catalog.categories() if c.kind == kind)
        for kind in CategoryKind
    }
    one = selection((first, second, categories))
    data = one.evidence.data
    data["image_sha256"] = "b" * 64
    data["file_sha256"] = "4" * 64
    two = replace(one, evidence=OcrEvidence(canonical(data)), acknowledged_duplicate=True)
    return one, two, first, second


def test_batch_is_atomic_replayable_and_preserved_with_settings_in_backup(
    session, chosen, tmp_path
):
    one, two, account, _ = chosen
    value = OcrPreferences(
        confirm_fields=False,
        review_before_save=False,
        transfer_parties=(TransferParty("Вымышленный родственник", account.id),),
    )
    revision = session.ocr_settings.save(value, None)
    assert session.ocr_settings.load() == (value, revision)
    command = str(uuid4())
    result = session.ocr_importing.confirm_many((one, two), command_id=command)
    assert len(result) == 2 and len(session.ledger.operations()) == 2
    assert session.ocr_importing.confirm_many((one, two), command_id=command) == result
    assert session.ocr_importing.preview(one).status == OcrImportStatus.DUPLICATE
    session.check_integrity()
    backup = create_backup(session, tmp_path / "batch.pfbackup", BACKUP_PASSWORD)
    restored, _ = restore_backup(backup, tmp_path / "new.pfvault", BACKUP_PASSWORD, PASSWORD)
    try:
        assert restored.replica_id != session.replica_id
        assert restored.ocr_settings.load() == (value, revision)
        assert restored.ocr_importing.confirm_many((one, two), command_id=command) == result
        restored.check_integrity()
    finally:
        restored.close()


def test_failure_after_first_row_rolls_back_whole_batch(session, chosen, monkeypatch):
    one, two, _, _ = chosen
    repo = session.ocr_importing._repository
    original = repo._confirm_in_transaction
    calls = []

    def fail(connection, value, command):
        result = original(connection, value, command)
        calls.append(True)
        if len(calls) == 2:
            raise OSError("Вымышленный отказ второй строки")
        return result

    monkeypatch.setattr(repo, "_confirm_in_transaction", fail)
    before = counts(session)
    with pytest.raises(OSError):
        session.ocr_importing.confirm_many((one, two))
    assert counts(session) == before
    with session.engine.connect() as connection:
        assert connection.execute(text("SELECT count(*) FROM ocr_batches")).scalar_one() == 0
        assert connection.execute(text("SELECT count(*) FROM ocr_batch_items")).scalar_one() == 0
    session.check_integrity()


def test_preview_detects_internal_possible_duplicate_and_rejects_unresolved(session, chosen):
    one, two, _, _ = chosen
    previews = session.ocr_importing.preview_many((one, replace(two, acknowledged_duplicate=False)))
    assert [p.status for p in previews] == [OcrImportStatus.NEW, OcrImportStatus.POSSIBLE]
    with pytest.raises(ValidationError):
        session.ocr_importing.confirm_many((one, replace(two, acknowledged_duplicate=False)))
    assert not session.ledger.operations()


def test_exact_row_cannot_be_added_twice_in_one_batch(session, chosen):
    one, _, _, _ = chosen
    before = counts(session)
    with pytest.raises(ValidationError):
        session.ocr_importing.confirm_many((one, one))
    assert counts(session) == before


def test_settings_are_versioned_stale_changes_rejected_and_history_immutable(session, chosen):
    _, _, account, _ = chosen
    first = OcrPreferences(transfer_parties=(TransferParty("Вымышленное имя", account.id),))
    revision = session.ocr_settings.save(first, None)
    second = replace(first, confirm_fields=False)
    newer = session.ocr_settings.save(second, revision)
    assert session.ocr_settings.load() == (second, newer)
    with pytest.raises(ValidationError):
        session.ocr_settings.save(first, revision)
    from sqlalchemy.exc import DatabaseError

    with pytest.raises(DatabaseError), session.engine.begin() as connection:
        connection.execute(text("DELETE FROM ocr_settings_versions WHERE id=:id"), {"id": revision})
    session.check_integrity()


def test_new_user_catalog_has_no_banks(session):
    assert session.catalog.institutions() == []
    assert session.catalog.categories()


def test_same_original_file_cannot_bypass_repeat_guard_after_normalization_change(session, chosen):
    one, _, _, _ = chosen
    session.ocr_importing.confirm(one)
    changed = one.evidence.data
    changed["image_sha256"] = "c" * 64
    same_file = replace(one, evidence=OcrEvidence(canonical(changed)), acknowledged_duplicate=True)
    assert session.ocr_importing.preview(same_file).status == OcrImportStatus.CONFLICT
    with pytest.raises(ValidationError):
        session.ocr_importing.confirm(same_file)
    assert len(session.ledger.operations()) == 1
