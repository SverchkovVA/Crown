"""Подготовка локальных доказательств происхождения OCR-DLL по распакованным пакетам.

Не распаковывает архивы, не загружает DLL и не обращается к сети. Совпадение
байтов не подтверждает подлинность пакета, полноту уведомлений или разрешение
распространения. Исходный runtime не изменяется.
"""

import argparse
import hashlib
import json
import re
from itertools import islice
from pathlib import Path, PurePosixPath
from typing import Any

MAX_METADATA_BYTES = 2 * 1024 * 1024
MAX_NATIVE_BYTES = 256 * 1024 * 1024
MAX_PACKAGE_DIRECTORIES = 1024
REQUIRED_FIELDS = ("pkgname", "pkgver", "arch")
SHA256 = re.compile(r"[0-9a-f]{64}")


def _safe_file(path: Path, root: Path, maximum: int) -> Path:
    path = path.absolute()
    if not path.is_relative_to(root) or not path.resolve(strict=True).is_relative_to(root):
        raise ValueError("Файл пакета выходит за указанный каталог.")
    current = path
    while current != root:
        if current.is_symlink() or current.is_junction():
            raise ValueError("Ссылки и соединения каталогов пакета не обрабатываются.")
        parent = current.parent
        if parent == current:
            raise ValueError("Неверная граница каталога пакета.")
        current = parent
    if not path.is_file() or path.stat().st_size > maximum:
        raise ValueError("Неверный тип или размер файла пакета.")
    return path


def _bounded_file(path: Path, root: Path, maximum: int) -> bytes:
    path = _safe_file(path, root, maximum)
    with path.open("rb") as source:
        value = source.read(maximum + 1)
    if len(value) > maximum:
        raise ValueError("Файл пакета изменился или превышает допустимый размер.")
    return value


def _native_hash(path: Path, root: Path) -> str:
    path = _safe_file(path, root, MAX_NATIVE_BYTES)
    digest = hashlib.sha256()
    count = 0
    with path.open("rb") as source:
        while chunk := source.read(1024 * 1024):
            count += len(chunk)
            if count > MAX_NATIVE_BYTES:
                raise ValueError("Библиотека пакета превышает допустимый размер.")
            digest.update(chunk)
    return digest.hexdigest()


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("В манифесте повторяется поле.")
        result[key] = value
    return result


def native_requirements(manifest: Path) -> dict[str, str]:
    manifest = manifest.resolve(strict=True)
    raw = _bounded_file(manifest, manifest.parent, MAX_METADATA_BYTES)
    try:
        data = json.loads(raw.decode("utf-8"), object_pairs_hook=_unique_object)
    except (UnicodeError, json.JSONDecodeError, RecursionError) as error:
        raise ValueError("Не удалось прочитать манифест OCR.") from error
    if (
        type(data) is not dict
        or type(data.get("format")) is not int
        or data["format"] != 1
        or type(data.get("file_sha256")) is not list
    ):
        raise ValueError("В манифесте нет списка SHA-256 файлов.")
    rows = data["file_sha256"]
    if not 1 <= len(rows) <= 1024:
        raise ValueError("Неверное число файлов манифеста OCR.")
    result = {}
    seen = set()
    for row in rows:
        if (
            type(row) is not list
            or len(row) != 2
            or type(row[0]) is not str
            or type(row[1]) is not str
        ):
            raise ValueError("Неверная запись файла манифеста OCR.")
        name, digest = row
        relative = PurePosixPath(name)
        if (
            not name
            or "\\" in name
            or "\x00" in name
            or relative.is_absolute()
            or ".." in relative.parts
            or str(relative) != name
            or ":" in name
            or not SHA256.fullmatch(digest)
            or name.casefold() in seen
        ):
            raise ValueError("Небезопасное имя, повтор или неверный SHA-256 файла OCR.")
        seen.add(name.casefold())
        if relative.suffix.lower() not in (".exe", ".dll"):
            continue
        if len(relative.parts) != 1:
            raise ValueError("Нативные файлы OCR должны находиться в корне runtime.")
        result[name] = digest
    if not result:
        raise ValueError("В манифесте нет нативных файлов OCR.")
    return result


def package_metadata(folder: Path, root: Path) -> dict[str, Any]:
    raw = _bounded_file(folder / ".PKGINFO", root, MAX_METADATA_BYTES)
    try:
        text = raw.decode("utf-8")
    except UnicodeError as error:
        raise ValueError("Метаданные пакета должны содержать UTF-8.") from error
    if "\x00" in text:
        raise ValueError("Метаданные пакета содержат нулевой символ.")
    fields: dict[str, list[str]] = {}
    for line in text.splitlines():
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        key, separator, value = line.partition(" = ")
        if not separator:
            raise ValueError("Неверная строка .PKGINFO.")
        if key not in (*REQUIRED_FIELDS, "license"):
            continue
        if not value or len(value) > 240:
            raise ValueError("Неверное значение .PKGINFO.")
        fields.setdefault(key, []).append(value)
    if any(len(fields.get(key, [])) != 1 for key in REQUIRED_FIELDS):
        raise ValueError("Пакет должен иметь по одному имени, версии и архитектуре.")
    metadata: dict[str, Any] = {key: fields[key][0] for key in REQUIRED_FIELDS}
    metadata["licenses_declared_in_pkginfo"] = sorted(set(fields.get("license", [])))
    metadata["pkginfo_sha256"] = hashlib.sha256(raw).hexdigest()
    buildinfo = folder / ".BUILDINFO"
    if buildinfo.exists() or buildinfo.is_symlink() or buildinfo.is_junction():
        metadata["buildinfo_sha256"] = hashlib.sha256(
            _bounded_file(buildinfo, root, MAX_METADATA_BYTES)
        ).hexdigest()
    else:
        metadata["buildinfo_sha256"] = None
    metadata["package_signature_verified"] = False
    metadata["source_archive_verified"] = False
    metadata["notices_reviewed"] = False
    return metadata


def collect_evidence(manifest: Path, packages: Path) -> dict[str, Any]:
    required = native_requirements(manifest)
    root = packages.resolve(strict=True)
    if not root.is_dir() or packages.is_symlink() or packages.is_junction():
        raise ValueError("Укажите обычный каталог распакованных пакетов.")
    matches: dict[str, list[dict[str, Any]]] = {name: [] for name in required}
    mismatch_counts = {name: 0 for name in required}
    package_count = 0
    skipped = 0
    folders = list(islice(root.iterdir(), MAX_PACKAGE_DIRECTORIES + 1))
    if len(folders) > MAX_PACKAGE_DIRECTORIES:
        raise ValueError("Слишком много объектов в каталоге распакованных пакетов.")
    for folder in sorted(folders, key=lambda path: path.name):
        if folder.is_symlink() or folder.is_junction():
            raise ValueError("Каталоги пакетов со ссылками не обрабатываются.")
        if not folder.is_dir():
            skipped += 1
            continue
        pkginfo = folder / ".PKGINFO"
        if not pkginfo.exists() and not pkginfo.is_symlink():
            skipped += 1
            continue
        metadata = package_metadata(folder, root)
        package_count += 1
        for name, expected in required.items():
            binary = folder / "mingw64" / "bin" / name
            if not binary.exists() and not binary.is_symlink() and not binary.is_junction():
                continue
            actual = _native_hash(binary, root)
            if actual != expected:
                mismatch_counts[name] += 1
                continue
            matches[name].append(
                {
                    "package": metadata,
                    "relative_binary": str(binary.relative_to(root)).replace("\\", "/"),
                }
            )
    records = [
        {
            "path": name,
            "sha256": expected,
            "byte_match_found": bool(matches[name]),
            "matching_packages": matches[name],
            "different_byte_candidates": mismatch_counts[name],
        }
        for name, expected in required.items()
    ]
    count = sum(bool(value) for value in matches.values())
    return {
        "format": 1,
        "status": "byte_matches_only",
        "native_files": len(required),
        "native_byte_matches": count,
        "native_unmatched": len(required) - count,
        "package_directories": package_count,
        "skipped_objects_without_pkginfo": skipped,
        "license_audit_complete": False,
        "application_bundled": False,
        "scope": (
            "Только сопоставление байтов с распакованными MSYS2-пакетами mingw64. "
            "Собственные Tesseract EXE/DLL и Ubuntu MinGW требуют отдельных доказательств. "
            "Подписи пакетов, полные уведомления и исходники здесь не подтверждаются."
        ),
        "records": records,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", required=True, type=Path)
    parser.add_argument("--packages", required=True, type=Path)
    parser.add_argument("--report", required=True, type=Path)
    args = parser.parse_args(argv)
    try:
        result = collect_evidence(args.manifest, args.packages)
        if args.report.suffix.lower() != ".json":
            raise ValueError("Отчёт должен иметь расширение .json.")
        encoded = (json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode(
            "utf-8"
        )
        with args.report.open("xb") as output:
            output.write(encoded)
    except (OSError, ValueError):
        parser.exit(
            2,
            "Не удалось подготовить отчёт. Проверьте технические входные файлы "
            "и новое имя отчёта.\n",
        )
    print(
        f"Отчёт создан. Совпало нативных файлов: {result['native_byte_matches']} "
        f"из {result['native_files']}. Лицензионная проверка не завершена."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
