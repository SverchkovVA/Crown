from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

from tools.windows_payload import _check_name, measure, report


def _bundle(tmp_path: Path) -> Path:
    bundle = tmp_path / "Сборка с пробелом"
    internal = bundle / "_internal"
    internal.mkdir(parents=True)
    (bundle / "PersonalFinance.exe").write_bytes(b"fake-exe")
    (internal / "tool.dll").write_bytes(b"fake-dll")
    return bundle


def test_windows_payload_lists_order_sizes_and_hashes(tmp_path: Path) -> None:
    bundle = _bundle(tmp_path)
    manifest = report(bundle)
    assert manifest["file_count"] == 2
    assert manifest["bytes"] == len(b"fake-exefake-dll")
    records = measure(bundle)
    assert [row.path for row in records] == ["PersonalFinance.exe", "_internal/tool.dll"]
    assert records[1].sha256 == hashlib.sha256(b"fake-dll").hexdigest()
    (bundle / "_internal" / "tool.dll").write_bytes(b"new-data")
    assert report(bundle) != manifest


@pytest.mark.parametrize("extra", ["ocr-runtime", "Библиотека.pfvault"])
def test_payload_requires_unmodified_top_level(tmp_path: Path, extra: str) -> None:
    bundle = _bundle(tmp_path)
    (bundle / extra).mkdir()
    with pytest.raises(ValueError, match="чистый каталог PyInstaller"):
        measure(bundle)


@pytest.mark.parametrize(
    "name", ["key.json", "Ledger.sqlite3", "Архив.pfbackup", "COM1.ini", "file$"]
)
def test_rejects_private_or_invalid_payload_names(name: str) -> None:
    with pytest.raises(ValueError, match="недопустимое имя"):
        _check_name(Path(name))


def test_rejects_reparse_point_before_reading(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    bundle = _bundle(tmp_path)
    other = bundle / "_internal" / "tool.dll"
    original_info = Path.lstat

    class Reparse:
        st_mode = other.stat().st_mode
        st_file_attributes = 0x400

    monkeypatch.setattr(
        Path, "lstat", lambda path: Reparse() if path == other else original_info(path)
    )
    with pytest.raises(ValueError, match="точку повторной обработки"):
        measure(bundle)
