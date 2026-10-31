import pytest

from personal_finance.application.vault import create_vault, open_vault
from personal_finance.domain.models import AccountKind
from personal_finance.infrastructure.backup.container import (
    create_backup,
    restore_backup,
    verify_backup,
)


@pytest.mark.parametrize("password", ["x", "Ж" * 1500], ids=["one_character", "long_unicode"])
def test_password_policy_applies_to_book_backup_and_new_folder_restore(tmp_path, password):
    root = tmp_path / "source.pfvault"
    session, recovery = create_vault(root, "Синтетическая книга", password)
    backup_password = password + "b"
    restored_password = password + "r"
    try:
        account = session.catalog.add_account("Синтетический счёт", AccountKind.CASH)
        before = (root / "data/key.json").read_bytes()
        archive = tmp_path / "test.pfbackup"
        create_backup(session, archive, backup_password)
        verify_backup(archive, backup_password)
        restored, restored_recovery = restore_backup(
            archive, tmp_path / "restored.pfvault", backup_password, restored_password
        )
        try:
            assert restored.catalog.accounts()[0].id == account.id
            restored.check_integrity()
        finally:
            restored.close()
        assert (root / "data/key.json").read_bytes() == before
        session.close()
        for secret, by_recovery in [(password, False), (recovery, True)]:
            opened = open_vault(root, secret, recovery=by_recovery)
            opened.close()
        for secret, by_recovery in [(restored_password, False), (restored_recovery, True)]:
            opened = open_vault(tmp_path / "restored.pfvault", secret, recovery=by_recovery)
            opened.close()
    finally:
        session.close()
