"""Потоковый защищённый контейнер. Временные файлы базы остаются зашифрованными."""

import hashlib
import json
import os
import struct
import tempfile
from collections.abc import Callable
from pathlib import Path
from typing import Any, BinaryIO
from uuid import UUID, uuid4

from nacl import bindings
from nacl.exceptions import CryptoError
from sqlalchemy import text

from personal_finance.application.catalog import utc_now
from personal_finance.application.vault import (
    VaultSession,
    publish_data,
    sync_directory,
    write_private,
)
from personal_finance.domain.errors import (
    FinanceError,
    InvalidCredentials,
    UnsupportedFormat,
    VaultError,
)
from personal_finance.infrastructure.persistence.migration_runner import SCHEMA_HEAD
from personal_finance.infrastructure.security.keys import (
    canonical_json,
    decode,
    derive_key,
    encode,
    kdf_settings,
    make_manifest,
    validate_password,
)

MAGIC = b"PFBAK\x00\x01\n"
CHUNK = 1024 * 1024
MAX_DATABASE = 10 * 1024**3
STREAM_OVERHEAD = bindings.crypto_secretstream_xchacha20poly1305_ABYTES
FINAL = bindings.crypto_secretstream_xchacha20poly1305_TAG_FINAL
MESSAGE = bindings.crypto_secretstream_xchacha20poly1305_TAG_MESSAGE


def _exact(stream: BinaryIO, length: int) -> bytes:
    data = stream.read(length)
    if len(data) != length:
        raise VaultError("Резервная копия обрывается до завершения данных.")
    return data


def _record(stream: BinaryIO) -> bytes:
    length = struct.unpack(">I", _exact(stream, 4))[0]
    if not STREAM_OVERHEAD <= length <= CHUNK + STREAM_OVERHEAD:
        raise UnsupportedFormat("Неподдерживаемый размер блока резервной копии.")
    return _exact(stream, length)


def _put(stream: BinaryIO, data: bytes) -> None:
    stream.write(struct.pack(">I", len(data)))
    stream.write(data)


def _read(
    source: Path, password: str, sink: Callable[[bytes], Any] | None = None
) -> dict[str, Any]:
    try:
        with source.open("rb") as stream:
            if _exact(stream, len(MAGIC)) != MAGIC:
                raise UnsupportedFormat("Это не поддерживаемая резервная копия.")
            length = struct.unpack(">I", _exact(stream, 4))[0]
            if not 1 <= length <= 4096:
                raise UnsupportedFormat("Заголовок резервной копии не поддерживается.")
            raw_header = _exact(stream, length)
            header = json.loads(raw_header)
            if (
                not isinstance(header, dict)
                or set(header) != {"format", "kdf", "stream_header"}
                or type(header["format"]) is not int
                or header["format"] != 1
            ):
                raise UnsupportedFormat("Версия резервной копии не поддерживается.")
            key = derive_key(password, header["kdf"])
            state = bindings.crypto_secretstream_xchacha20poly1305_state()
            bindings.crypto_secretstream_xchacha20poly1305_init_pull(
                state,
                decode(
                    header["stream_header"],
                    bindings.crypto_secretstream_xchacha20poly1305_HEADERBYTES,
                ),
                key,
            )
            metadata_raw, tag = bindings.crypto_secretstream_xchacha20poly1305_pull(
                state, _record(stream), raw_header
            )
            if tag != MESSAGE or len(metadata_raw) > 8192:
                raise UnsupportedFormat("Описание содержимого копии не поддерживается.")
            metadata = json.loads(metadata_raw)
            if not isinstance(metadata, dict) or set(metadata) != {
                "book_id",
                "database_key",
                "database_size",
                "database_sha256",
                "schema",
                "created_at",
            }:
                raise UnsupportedFormat("Описание содержимого копии повреждено.")
            if (
                type(metadata["database_size"]) is not int
                or not 0 < metadata["database_size"] <= MAX_DATABASE
                or metadata["schema"] != SCHEMA_HEAD
                or not isinstance(metadata["book_id"], str)
                or str(UUID(metadata["book_id"])) != metadata["book_id"]
            ):
                raise UnsupportedFormat("Содержимое копии не поддерживается этой версией.")
            decode(metadata["database_key"], 32)
            digest = hashlib.sha256()
            total = 0
            while True:
                plaintext, tag = bindings.crypto_secretstream_xchacha20poly1305_pull(
                    state, _record(stream), raw_header
                )
                if tag == FINAL:
                    if plaintext or stream.read(1):
                        raise VaultError("Копия содержит лишние данные после завершения.")
                    break
                if tag != MESSAGE or not plaintext:
                    raise VaultError("Повреждена последовательность блоков копии.")
                total += len(plaintext)
                if total > metadata["database_size"]:
                    raise VaultError("Размер содержимого копии не соответствует описанию.")
                digest.update(plaintext)
                if sink is not None:
                    sink(plaintext)
            if (
                total != metadata["database_size"]
                or digest.hexdigest() != metadata["database_sha256"]
            ):
                raise VaultError("Контрольная сумма содержимого копии не совпадает.")
            return metadata
    except CryptoError as exc:
        raise InvalidCredentials("Неверный пароль резервной копии или файл повреждён.") from exc
    except (ValueError, TypeError, KeyError, UnicodeError) as exc:
        raise UnsupportedFormat("Структура резервной копии повреждена.") from exc


def verify_backup(source: Path, password: str) -> dict[str, Any]:
    """Проверяет все блоки и финальный тег, не создавая восстановленную книгу."""
    metadata = _read(source, password)
    return {name: value for name, value in metadata.items() if name != "database_key"}


def _publish_file(temporary: Path, destination: Path) -> None:
    if os.name == "nt":
        # Windows rename отказывает при существующем назначении.
        temporary.rename(destination)
    else:
        # POSIX rename заменил бы чужой файл; link создаёт имя только при его отсутствии.
        os.link(temporary, destination)
        temporary.unlink()
    sync_directory(destination.parent)


def create_backup(session: VaultSession, destination: Path, password: str) -> Path:
    validate_password(password)
    destination = destination.absolute()
    if destination.exists() or destination.resolve().is_relative_to(session.root.resolve()):
        raise VaultError("Выберите новое имя копии за пределами папки открытой книги.")
    if not destination.parent.is_dir():
        raise VaultError("Папка для резервной копии не существует.")
    with (
        session.mutex,
        tempfile.TemporaryDirectory(prefix=".pf-backup-", dir=destination.parent) as tmp,
    ):
        session.ensure_open()
        snapshot = Path(tmp) / "snapshot.sqlite3"
        session.snapshot(snapshot)
        size = snapshot.stat().st_size
        if not 0 < size <= MAX_DATABASE:
            raise VaultError("Размер базы превышает предел этой версии резервирования.")
        with snapshot.open("rb") as source:
            digest = hashlib.file_digest(source, "sha256").hexdigest()
        settings = kdf_settings()
        state = bindings.crypto_secretstream_xchacha20poly1305_state()
        stream_header = bindings.crypto_secretstream_xchacha20poly1305_init_push(
            state, derive_key(password, settings)
        )
        raw_header = canonical_json(
            {"format": 1, "kdf": settings, "stream_header": encode(stream_header)}
        )
        metadata = canonical_json(
            {
                "book_id": session.book_id,
                "database_key": encode(session.backup_key()),
                "database_size": size,
                "database_sha256": digest,
                "schema": SCHEMA_HEAD,
                "created_at": utc_now(),
            }
        )
        temporary = Path(tmp) / "backup.partial"
        descriptor = os.open(temporary, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        with os.fdopen(descriptor, "wb") as output, snapshot.open("rb") as source:
            output.write(MAGIC)
            output.write(struct.pack(">I", len(raw_header)))
            output.write(raw_header)
            _put(
                output,
                bindings.crypto_secretstream_xchacha20poly1305_push(
                    state, metadata, raw_header, MESSAGE
                ),
            )
            while chunk := source.read(CHUNK):
                _put(
                    output,
                    bindings.crypto_secretstream_xchacha20poly1305_push(
                        state, chunk, raw_header, MESSAGE
                    ),
                )
            _put(
                output,
                bindings.crypto_secretstream_xchacha20poly1305_push(state, b"", raw_header, FINAL),
            )
            output.flush()
            os.fsync(output.fileno())
        verify_backup(temporary, password)
        _publish_file(temporary, destination)
    return destination


def restore_backup(
    source: Path, destination: Path, backup_password: str, new_password: str
) -> tuple[VaultSession, str]:
    """Восстанавливает только в новую папку; старые книги никогда не заменяются."""
    from personal_finance.application.vault import open_vault

    validate_password(new_password)
    destination = destination.absolute()
    if destination.exists() or not destination.parent.is_dir():
        raise VaultError("Выберите несуществующую папку внутри доступного каталога.")
    with tempfile.TemporaryDirectory(prefix=".pf-restore-", dir=destination.parent) as temporary:
        staged_root = Path(temporary)
        staged_data = staged_root / "data"
        staged_data.mkdir(mode=0o700)
        database = staged_data / "ledger.sqlite3"
        descriptor = os.open(database, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        with os.fdopen(descriptor, "wb") as output:
            metadata = _read(source, backup_password, output.write)
            output.flush()
            os.fsync(output.fileno())
        key = decode(metadata["database_key"], 32)
        manifest, recovery = make_manifest(metadata["book_id"], key, new_password)
        session = VaultSession(staged_root, metadata["book_id"], key)
        try:
            session.check_integrity()
            # Копия сохраняет историю, но новое устройство получает новую идентичность реплики.
            replica_id = str(uuid4())
            with session.engine.begin() as connection:
                connection.execute(
                    text("INSERT INTO replicas (id,book_id) VALUES (:id,:book)"),
                    {"id": replica_id, "book": session.book_id},
                )
                connection.execute(
                    text("UPDATE local_state SET replica_id=:id WHERE id=1"), {"id": replica_id}
                )
            session.check_integrity()
        except FinanceError:
            raise
        finally:
            session.close()
        write_private(staged_data / "key.json", manifest)
        publish_data(staged_data, destination)
    return open_vault(destination, new_password), recovery
