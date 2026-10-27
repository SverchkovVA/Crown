"""Версии и отпечаток исходников; без имён пользователя и абсолютных путей."""

import hashlib
import json
from pathlib import Path

from personal_finance.bootstrap.diagnostics import runtime_info


def source_fingerprint(root: Path) -> str:
    files = {
        root / name
        for name in (
            "pyproject.toml",
            "uv.lock",
            ".python-version",
            ".gitattributes",
            "check-windows.cmd",
        )
    }
    for folder, pattern in [
        ("src", "*.py"),
        ("tests", "*.py"),
        ("tools", "*.py"),
        ("packaging", "*.ps1"),
        ("packaging", "*.psm1"),
        ("packaging", "*.spec"),
        ("packaging", "*.json"),
    ]:
        files.update((root / folder).rglob(pattern))
    digest = hashlib.sha256()
    for path in sorted(files, key=lambda item: item.relative_to(root).as_posix()):
        digest.update(path.relative_to(root).as_posix().encode())
        digest.update(b"\0")
        digest.update(hashlib.sha256(path.read_bytes().replace(b"\r\n", b"\n")).digest())
    return digest.hexdigest()


if __name__ == "__main__":
    root = Path(__file__).resolve().parents[1]
    print(
        json.dumps(
            {**runtime_info(), "source_sha256": source_fingerprint(root)},
            ensure_ascii=False,
            indent=2,
        )
    )
