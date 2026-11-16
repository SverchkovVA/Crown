from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from tools.windows_installer import BUNDLE, EXE, MANIFEST, prepare
from tools.windows_payload import report


def _checked_build(tmp_path: Path) -> Path:
    root = tmp_path / "Учебная сборка"
    app = root / BUNDLE
    child = app / "_internal" / "Qt files"
    child.mkdir(parents=True)
    (app / EXE).write_bytes(b"fake-exe")
    (child / "theme.dll").write_bytes(b"fake-dll")
    manifest = json.dumps(report(app), ensure_ascii=False).encode()
    (root / MANIFEST).write_bytes(manifest)
    receipt = {
        "format": 1,
        "status": "passed",
        "executable": (BUNDLE / EXE).as_posix(),
        "payload_manifest": MANIFEST,
        "payload_sha256": hashlib.sha256(manifest).hexdigest(),
        "exe_sha256": hashlib.sha256(b"fake-exe").hexdigest(),
    }
    (root / "build-result.json").write_text(json.dumps(receipt), encoding="utf-8")
    return root


def test_prepares_explicit_install_and_uninstall_lists(tmp_path: Path) -> None:
    build = _checked_build(tmp_path)
    output = tmp_path / "готово"
    output.mkdir()
    count, digest = prepare(build, output)
    assert count == 2
    config = (output / "payload.nsh").read_text(encoding="utf-8-sig")
    assert digest in config
    assert 'File "${CROWN_BUNDLE}\\_internal\\Qt files\\theme.dll"' in config
    assert 'Delete "${ROOT}\\_internal\\Qt files\\theme.dll"' in config
    assert "RMDir /r" not in config and "File /r" not in config
    assert config.index('RMDir "${ROOT}\\_internal\\Qt files"') < config.index(
        'RMDir "${ROOT}\\_internal"'
    )
    with pytest.raises(FileExistsError):
        prepare(build, output)


@pytest.mark.parametrize(
    ("file", "message"),
    [
        (MANIFEST, "Ведомость отличается"),
        ("app/PersonalFinance/_internal/Qt files/theme.dll", "Файлы приложения"),
    ],
)
def test_refuses_changed_build(tmp_path: Path, file: str, message: str) -> None:
    build = _checked_build(tmp_path)
    (build / file).write_bytes(b"changed")
    with pytest.raises(ValueError, match=message):
        prepare(build, tmp_path)
    assert not (tmp_path / "payload.nsh").exists()
