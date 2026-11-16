from __future__ import annotations

import hashlib
import json
from pathlib import Path


def test_windows_foundation_notices_match_manifest() -> None:
    root = Path(__file__).resolve().parents[2] / "docs/legal/windows-foundation"
    manifest = json.loads((root / "manifest.json").read_bytes())
    assert manifest["format"] == 1
    files = {file.relative_to(root).as_posix() for file in root.rglob("*") if file.is_file()}
    files.remove("manifest.json")
    assert files == {entry["path"] for entry in manifest["files"]}
    assert len(files) == len(manifest["files"])
    for entry in manifest["files"]:
        data = (root / entry["path"]).read_bytes()
        assert hashlib.sha256(data).hexdigest() == entry["sha256"], entry["path"]
