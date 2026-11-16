"""Автономная проверка подписи метаданных и файла выпуска Windows."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from nacl.exceptions import BadSignatureError
from nacl.signing import VerifyKey

_MANIFEST_LIMIT = 4096
_PLATFORM = "windows-x64"
_VERSION = re.compile(r"(0|[1-9][0-9]{0,8})\.(0|[1-9][0-9]{0,8})\.(0|[1-9][0-9]{0,8})")
_SHA256 = re.compile(r"[a-f0-9]{64}")
_UTC_TIMESTAMP = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}Z")
_FIELDS = frozenset(
    {
        "format",
        "platform",
        "version",
        "expires_utc",
        "installer_bytes",
        "installer_sha256",
        "source_sha256",
    }
)


@dataclass(frozen=True, slots=True)
class WindowsRelease:
    version: str
    installer_bytes: int
    installer_sha256: str
    source_sha256: str
    expires_at: datetime

    @property
    def filename(self) -> str:
        return f"Crown-{self.version}-windows-x64-setup.exe"


def _unique_fields(items: list[tuple[str, object]]) -> dict[str, object]:
    fields: dict[str, object] = {}
    for field, value in items:
        if field in fields:
            raise ValueError("В ведомости выпуска повторяется поле.")
        fields[field] = value
    return fields


def _version(value: str) -> tuple[int, int, int]:
    match = _VERSION.fullmatch(value)
    if match is None:
        raise ValueError("Номер стабильного выпуска должен быть в виде X.Y.Z.")
    return int(match[1]), int(match[2]), int(match[3])


def _digest(value: object) -> str:
    if not isinstance(value, str) or _SHA256.fullmatch(value) is None:
        raise ValueError("В ведомости выпуска неверный SHA-256.")
    return value


def _expiration(value: object) -> datetime:
    if not isinstance(value, str) or _UTC_TIMESTAMP.fullmatch(value) is None:
        raise ValueError("В ведомости выпуска неверный срок действия UTC.")
    try:
        return datetime.fromisoformat(value)
    except ValueError as exc:
        raise ValueError("В ведомости выпуска неверный срок действия UTC.") from exc


def verify_manifest(
    payload: bytes, signature: bytes, public_key: bytes, *, minimum_version: str, now: datetime
) -> WindowsRelease:
    if not 0 < len(payload) <= _MANIFEST_LIMIT or len(signature) != 64 or len(public_key) != 32:
        raise ValueError("Неверный размер ведомости, подписи или открытого ключа.")
    try:
        VerifyKey(public_key).verify(payload, signature)
    except BadSignatureError as exc:
        raise ValueError("Подпись ведомости выпуска не подтверждена.") from exc

    try:
        data = json.loads(payload.decode("utf-8"), object_pairs_hook=_unique_fields)
    except (UnicodeError, json.JSONDecodeError, RecursionError) as exc:
        raise ValueError("Ведомость выпуска должна быть JSON в UTF-8.") from exc
    if not isinstance(data, dict) or data.keys() != _FIELDS:
        raise ValueError("Ведомость выпуска содержит неверный набор полей.")
    if type(data["format"]) is not int or data["format"] != 1 or data["platform"] != _PLATFORM:
        raise ValueError("Формат или платформа выпуска не поддерживается.")

    version = data["version"]
    if not isinstance(version, str) or _version(version) <= _version(minimum_version):
        raise ValueError("Версия выпуска должна быть новее указанной.")
    expires = _expiration(data["expires_utc"])
    if now.utcoffset() is None:
        raise ValueError("Для проверки нужен часовой пояс текущего времени.")
    if expires <= now:
        raise ValueError("Срок действия ведомости выпуска истёк.")
    size = data["installer_bytes"]
    if type(size) is not int or size <= 0:
        raise ValueError("В ведомости выпуска неверный размер установщика.")
    return WindowsRelease(
        version, size, _digest(data["installer_sha256"]), _digest(data["source_sha256"]), expires
    )


def verify_installer(path: Path, release: WindowsRelease) -> None:
    if path.name != release.filename:
        raise ValueError("Имя установщика не соответствует выпуску.")
    with path.open("rb") as stream:
        if os.fstat(stream.fileno()).st_size != release.installer_bytes:
            raise ValueError("Размер установщика отличается от подписанной ведомости.")
        if hashlib.file_digest(stream, "sha256").hexdigest() != release.installer_sha256:
            raise ValueError("SHA-256 установщика отличается от подписанной ведомости.")


def _read_limited(path: Path, limit: int) -> bytes:
    with path.open("rb") as stream:
        return stream.read(limit + 1)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, required=True, help="ведомость UTF-8")
    parser.add_argument("--signature", type=Path, required=True, help="отдельная подпись Ed25519")
    parser.add_argument(
        "--public-key", type=Path, required=True, help="проверенный открытый ключ Ed25519"
    )
    parser.add_argument(
        "--minimum-version", required=True, help="текущая или ранее принятая версия"
    )
    parser.add_argument("--installer", type=Path, required=True, help="локальный файл Windows x64")
    args = parser.parse_args()
    try:
        release = verify_manifest(
            _read_limited(args.manifest, _MANIFEST_LIMIT),
            _read_limited(args.signature, 64),
            _read_limited(args.public_key, 32),
            minimum_version=args.minimum_version,
            now=datetime.now(UTC),
        )
        verify_installer(args.installer, release)
    except OSError:
        parser.exit(1, "Не удалось прочитать файлы выпуска.\n")
    except ValueError as exc:
        parser.exit(1, f"{exc}\n")
    print(f"Проверена ведомость и SHA-256 установщика Crown {release.version} для Windows x64.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
