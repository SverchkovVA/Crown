from __future__ import annotations

import json
import os
import stat
import subprocess
from contextlib import closing
from pathlib import Path

import pytest

from personal_finance.application.vault import VaultSession, create_vault, open_vault
from personal_finance.domain.errors import InvalidCredentials, ValidationError, VaultError
from personal_finance.domain.models import AccountKind
from personal_finance.infrastructure.security import manifest_file
from tests.conftest import PASSWORD

NEW = "  учебная новая фраза  "


def test_change_password_preserves_data_and_recovery_code(tmp_path: Path) -> None:
    created, recovery = create_vault(tmp_path / "book.pfvault", "Учебная книга", PASSWORD)
    with closing(created) as session:
        root = session.root
        account_id = session.catalog.add_account("Учебный счёт", AccountKind.CASH).id
    manifest = root / "data/key.json"
    database = root / "data/ledger.sqlite3"
    original = database.read_bytes()
    before = json.loads(manifest.read_bytes())
    with closing(open_vault(root, PASSWORD)) as session:
        session.change_password(PASSWORD, NEW)
        assert session.catalog.accounts()[0].id == account_id
    after = json.loads(manifest.read_bytes())
    assert after["recovery_wrapped_key"] == before["recovery_wrapped_key"]
    assert after["book_id"] == before["book_id"] and after["format"] == before["format"]
    assert after["wrapped_key"] != before["wrapped_key"]
    assert database.read_bytes() == original
    assert list(manifest.parent.glob(".password-*.tmp")) == []
    for credential in (PASSWORD, NEW.strip()):
        with pytest.raises(InvalidCredentials):
            open_vault(root, credential)
    for credential, by_recovery in [(NEW, False), (recovery, True)]:
        with closing(open_vault(root, credential, recovery=by_recovery)) as reopened:
            assert reopened.catalog.accounts()[0].id == account_id
            reopened.check_integrity()


def test_invalid_input_and_failed_replace_keep_current_file(
    session: VaultSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = session.root
    manifest = root / "data/key.json"
    original = manifest.read_bytes()

    with pytest.raises(InvalidCredentials):
        session.change_password("другая фраза", NEW)
    with pytest.raises(ValidationError):
        session.change_password(PASSWORD, "")
    assert manifest.read_bytes() == original

    def failed_replace(source: Path, target: Path) -> None:
        assert source.parent == manifest.parent and target == manifest
        assert source.read_bytes() != original
        raise OSError("учебная ошибка записи")

    with monkeypatch.context() as patch, pytest.raises(VaultError, match="Не удалось обновить"):
        patch.setattr(manifest_file.os, "replace", failed_replace)
        session.change_password(PASSWORD, NEW)
    assert manifest.read_bytes() == original
    assert list(manifest.parent.glob(".password-*.tmp")) == []
    session.close()
    with pytest.raises(VaultError, match="закрыта"):
        session.change_password(PASSWORD, NEW)
    with closing(open_vault(root, PASSWORD)):
        pass


def test_change_password_does_not_replace_a_concurrently_modified_manifest(
    session: VaultSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    manifest = session.root / "data/key.json"
    edited = manifest.read_bytes() + b"\n"
    copy_access = manifest_file._copy_access

    def copy_then_edit(source: Path, target: Path, descriptor: int) -> None:
        copy_access(source, target, descriptor)
        manifest.write_bytes(edited)

    monkeypatch.setattr(manifest_file, "_copy_access", copy_then_edit)
    with pytest.raises(VaultError, match="Ключевой файл изменился"):
        session.change_password(PASSWORD, NEW)
    assert manifest.read_bytes() == edited
    assert list(manifest.parent.glob(".password-*.tmp")) == []


def test_change_password_reports_sync_failure_after_publication(
    session: VaultSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    def fail_sync(directory: Path) -> None:
        assert directory == session.root / "data"
        raise OSError("учебная ошибка записи каталога")

    monkeypatch.setattr("personal_finance.application.vault.sync_directory", fail_sync)
    with pytest.raises(VaultError, match="Парольная фраза заменена"):
        session.change_password(PASSWORD, NEW)
    session.close()
    with closing(open_vault(session.root, NEW)):
        pass


def test_change_password_keeps_key_file_permissions(session: VaultSession) -> None:
    manifest = session.root / "data/key.json"
    if os.name == "nt":
        before = _access_descriptor(manifest)
        session.change_password(PASSWORD, NEW)
        assert _access_descriptor(manifest) == before
        protected = _access_descriptor(manifest, protect=True)
        assert protected != before
        session.change_password(NEW, PASSWORD)
        assert _access_descriptor(manifest) == protected
    else:
        manifest.chmod(0o640)
        before_info = manifest.stat()
        session.change_password(PASSWORD, NEW)
        after_info = manifest.stat()
        assert (after_info.st_uid, after_info.st_gid) == (before_info.st_uid, before_info.st_gid)
        assert stat.S_IMODE(after_info.st_mode) == 0o640


def _access_descriptor(path: Path, *, protect: bool = False) -> str:
    script = (
        "$ErrorActionPreference = 'Stop'; "
        "$acl = [System.IO.File]::GetAccessControl($env:CROWN_TEST_MANIFEST); "
        "if ($env:CROWN_TEST_PROTECT -eq '1') { "
        "$acl.SetAccessRuleProtection($true, $true); "
        "[System.IO.File]::SetAccessControl($env:CROWN_TEST_MANIFEST, $acl); "
        "$acl = [System.IO.File]::GetAccessControl($env:CROWN_TEST_MANIFEST) }; "
        "$section = [System.Security.AccessControl.AccessControlSections]; "
        "$parts = $section::Access -bor $section::Owner -bor $section::Group; "
        "$raw = [System.Security.AccessControl.RawSecurityDescriptor]::new("
        "$acl.GetSecurityDescriptorBinaryForm(), 0); "
        "$auto = [System.Security.AccessControl.ControlFlags]::DiscretionaryAclAutoInherited; "
        "$raw.SetFlags($raw.ControlFlags -band (-bnot $auto)); "
        "$raw.GetSddlForm($parts)"
    )
    result = subprocess.run(
        ["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", script],
        check=False,
        capture_output=True,
        text=True,
        timeout=30,
        env={
            **os.environ,
            "CROWN_TEST_MANIFEST": str(path),
            "CROWN_TEST_PROTECT": str(int(protect)),
        },
    )
    assert result.returncode == 0, result.stderr
    return result.stdout.strip()
