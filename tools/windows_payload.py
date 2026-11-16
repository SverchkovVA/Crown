"""Побайтовая ведомость каталога Windows для установки и проверки."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import stat
from dataclasses import asdict, dataclass
from pathlib import Path

from personal_finance import __version__

_EXECUTABLE = "PersonalFinance.exe"
_SUPPORT = "_internal"
_FORBIDDEN_NAMES = frozenset(
    {"key.json", "ledger.sqlite3", "ledger.sqlite3-shm", "ledger.sqlite3-wal"}
)
_RESERVED_NAMES = frozenset(
    {"con", "prn", "aux", "nul", "conin$", "conout$"}
    | {f"{prefix}{value}" for prefix in ("com", "lpt") for value in "123456789¹²³"}
)


@dataclass(frozen=True, slots=True)
class PayloadFile:
    path: str
    size: int
    sha256: str


def _check_name(path: Path) -> None:
    name = path.name
    normalized = name.casefold()
    if (
        name.endswith((" ", "."))
        or any(character in '<>:"/\\|?*$' or ord(character) < 32 for character in name)
        or normalized.split(".", 1)[0].rstrip() in _RESERVED_NAMES
        or normalized in _FORBIDDEN_NAMES
        or normalized.endswith((".pfvault", ".pfbackup"))
    ):
        raise ValueError("В каталоге приложения находится недопустимое имя.")


def _file_info(path: Path) -> os.stat_result:
    metadata = path.lstat()
    if stat.S_ISLNK(metadata.st_mode) or (
        getattr(metadata, "st_file_attributes", 0) & stat.FILE_ATTRIBUTE_REPARSE_POINT
    ):
        raise ValueError("Каталог приложения содержит ссылку или точку повторной обработки.")
    return metadata


def _scan(folder: Path, root: Path) -> list[tuple[Path, int]]:
    entries: list[tuple[Path, int]] = []
    used: set[str] = set()
    for path in sorted(folder.iterdir(), key=lambda item: item.name):
        _check_name(path)
        normalized = path.name.casefold()
        if normalized in used:
            raise ValueError("Имена в каталоге приложения совпадают без учёта регистра.")
        used.add(normalized)
        metadata = _file_info(path)
        if stat.S_ISDIR(metadata.st_mode):
            entries.extend(_scan(path, root))
        elif stat.S_ISREG(metadata.st_mode):
            entries.append((path.relative_to(root), metadata.st_size))
        else:
            raise ValueError("В каталог приложения попал специальный файл.")
    return entries


def measure(root: Path) -> tuple[PayloadFile, ...]:
    if not stat.S_ISDIR(_file_info(root).st_mode):
        raise ValueError("Укажите каталог PyInstaller.")
    if {item.name for item in root.iterdir()} != {_EXECUTABLE, _SUPPORT} or not (
        stat.S_ISREG(_file_info(root / _EXECUTABLE).st_mode)
        and stat.S_ISDIR(_file_info(root / _SUPPORT).st_mode)
    ):
        raise ValueError("Ожидается чистый каталог PyInstaller с EXE и папкой _internal.")
    entries = _scan(root, root)
    if not any(path.parts[0] == _SUPPORT for path, _ in entries):
        raise ValueError("В каталоге приложения отсутствуют зависимости.")
    files = []
    for relative, size in sorted(entries, key=lambda row: row[0].as_posix()):
        with (root / relative).open("rb") as stream:
            digest = hashlib.file_digest(stream, "sha256").hexdigest()
        files.append(PayloadFile(relative.as_posix(), size, digest))
    return tuple(files)


def report(root: Path) -> dict[str, object]:
    files = measure(root)
    return {
        "format": 1,
        "application": __version__,
        "file_count": len(files),
        "bytes": sum(item.size for item in files),
        "files": [asdict(item) for item in files],
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("bundle", type=Path)
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    document = report(args.bundle)
    with args.output.open("x", encoding="utf-8", newline="\n") as handle:
        json.dump(document, handle, ensure_ascii=False, indent=2)
        handle.write("\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
