import hashlib
import tempfile

import pytest

from personal_finance.domain.errors import InvalidCredentials
from personal_finance.domain.ledger import OperationDraft, OperationKind
from personal_finance.domain.models import AccountKind, Money
from personal_finance.infrastructure.backup.container import create_backup, inspect_backup
from personal_finance.infrastructure.persistence.migration_runner import SCHEMA_HEAD
from tests.conftest import BACKUP_PASSWORD
from tests.integration.test_deposit_scenario_upgrade import create_at
from tests.unit.test_investment_positions import inputs
from tests.unit.test_investments import TODAY


def digest(path):
    return hashlib.sha256(path.read_bytes()).digest()


def test_backup_inspection_counts_archive_data_without_publishing_or_modifying_source(
    session, tmp_path, monkeypatch
):
    account = session.catalog.add_account("Учебный инвестиционный счёт", AccountKind.INVESTMENT)
    session.ledger.add(OperationDraft(OperationKind.OPENING, TODAY, account.id, Money(2000)))
    session.investment_positions.save(inputs(account))
    archive = create_backup(session, tmp_path / "portfolio.pfbackup", BACKUP_PASSWORD)
    session.catalog.add_account("Счёт создан позже архива", AccountKind.CASH)
    before = digest(archive), digest(session.database_path)
    staged = tmp_path / "staging"
    staged.mkdir()
    monkeypatch.setattr(tempfile, "tempdir", str(staged))

    contents = inspect_backup(archive, BACKUP_PASSWORD)

    assert contents.source_schema == contents.restore_schema == SCHEMA_HEAD
    assert contents.database_size > 0 and contents.created_at
    assert (contents.accounts, contents.operations, contents.investment_positions) == (1, 1, 1)
    assert "Учебный" not in repr(contents) and session.book_id not in repr(contents)
    assert (digest(archive), digest(session.database_path)) == before
    assert not list(staged.iterdir())


def test_wrong_password_cleans_staging_directory(session, tmp_path, monkeypatch):
    archive = create_backup(session, tmp_path / "portfolio.pfbackup", BACKUP_PASSWORD)
    staged = tmp_path / "staging"
    staged.mkdir()
    monkeypatch.setattr(tempfile, "tempdir", str(staged))

    with pytest.raises(InvalidCredentials):
        inspect_backup(archive, "Вымышленный неверный пароль")
    assert not list(staged.iterdir())


def test_inspection_reports_source_schema_without_upgrading_archive(tmp_path, monkeypatch):
    root = tmp_path / "legacy.pfvault"
    session, _ = create_at(monkeypatch, root, "0016")
    try:
        session.catalog.add_account("Учебный счёт", AccountKind.CASH)
        archive = create_backup(session, tmp_path / "legacy.pfbackup", BACKUP_PASSWORD)
        original = digest(archive), digest(session.database_path)

        contents = inspect_backup(archive, BACKUP_PASSWORD)

        assert (contents.source_schema, contents.restore_schema) == ("0016", SCHEMA_HEAD)
        assert (contents.accounts, contents.operations, contents.investment_positions) == (1, 0, 0)
        assert original == (digest(archive), digest(session.database_path))
    finally:
        session.close()
