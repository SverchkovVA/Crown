"""Версионированная обёртка случайного ключа базы: Argon2id и XChaCha20-Poly1305."""

import base64
import json
import secrets
from typing import Any

from nacl import pwhash
from nacl.exceptions import CryptoError
from nacl.secret import Aead

from personal_finance.domain.errors import InvalidCredentials, UnsupportedFormat, ValidationError

KDF_OPS = 3
KDF_MEMORY = 64 * 1024 * 1024
KEY_BYTES = 32
MAX_MANIFEST_BYTES = 4096


def canonical_json(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode(
        "utf-8"
    )


def encode(value: bytes) -> str:
    return base64.b64encode(value).decode("ascii")


def decode(value: object, size: int) -> bytes:
    try:
        if not isinstance(value, str):
            raise ValueError
        result = base64.b64decode(value, validate=True)
        if len(result) != size:
            raise ValueError
        return result
    except (ValueError, TypeError) as exc:
        raise UnsupportedFormat(
            "Формат защищённых данных повреждён или не поддерживается."
        ) from exc


def validate_password(password: str) -> None:
    if len(password) < 12 or len(password.encode("utf-8")) > 1024:
        raise ValidationError("Используйте парольную фразу от 12 символов (не более 1024 байт).")


def kdf_settings() -> dict[str, object]:
    return {
        "algorithm": "argon2id13",
        "opslimit": KDF_OPS,
        "memlimit": KDF_MEMORY,
        "salt": encode(secrets.token_bytes(pwhash.argon2id.SALTBYTES)),
    }


def derive_key(password: str, settings: object) -> bytes:
    if not isinstance(settings, dict) or set(settings) != {
        "algorithm",
        "opslimit",
        "memlimit",
        "salt",
    }:
        raise UnsupportedFormat("Неизвестный формат получения ключа.")
    if (
        settings["algorithm"] != "argon2id13"
        or type(settings["opslimit"]) is not int
        or type(settings["memlimit"]) is not int
        or settings["opslimit"] != KDF_OPS
        or settings["memlimit"] != KDF_MEMORY
    ):
        raise UnsupportedFormat("Параметры защиты не поддерживаются этой версией.")
    if len(password.encode("utf-8")) > 1024:
        raise InvalidCredentials("Не удалось открыть данные: проверьте пароль.")
    return pwhash.argon2id.kdf(
        KEY_BYTES,
        password.encode("utf-8"),
        decode(settings["salt"], pwhash.argon2id.SALTBYTES),
        opslimit=KDF_OPS,
        memlimit=KDF_MEMORY,
    )


def make_manifest(book_id: str, key: bytes, password: str) -> tuple[bytes, str]:
    validate_password(password)
    settings = kdf_settings()
    password_key = derive_key(password, settings)
    recovery_key = secrets.token_bytes(KEY_BYTES)
    aad = b"personal-finance/vault-key/v1/" + book_id.encode("ascii")
    manifest = {
        "format": 1,
        "book_id": book_id,
        "kdf": settings,
        "wrapped_key": encode(bytes(Aead(password_key).encrypt(key, aad))),
        "recovery_wrapped_key": encode(bytes(Aead(recovery_key).encrypt(key, aad))),
    }
    code = base64.b32encode(recovery_key).decode("ascii").rstrip("=")
    recovery = "PF1-" + "-".join(code[i : i + 4] for i in range(0, len(code), 4))
    return canonical_json(manifest), recovery


def unlock_manifest(raw: bytes, credential: str, *, recovery: bool = False) -> tuple[str, bytes]:
    from uuid import UUID

    try:
        if len(raw) > MAX_MANIFEST_BYTES:
            raise ValueError
        data = json.loads(raw)
        if not isinstance(data, dict) or set(data) != {
            "format",
            "book_id",
            "kdf",
            "wrapped_key",
            "recovery_wrapped_key",
        }:
            raise ValueError
        if type(data["format"]) is not int or data["format"] != 1:
            raise ValueError
        if not isinstance(data["book_id"], str):
            raise ValueError
        book_id = str(UUID(data["book_id"]))
        if book_id != data["book_id"]:
            raise ValueError
    except (ValueError, TypeError, UnicodeError, KeyError) as exc:
        raise UnsupportedFormat("Формат книги повреждён или не поддерживается.") from exc
    aad = b"personal-finance/vault-key/v1/" + book_id.encode("ascii")
    if recovery:
        try:
            if len(credential) > 100 or not credential.startswith("PF1-"):
                raise ValueError
            code = credential[4:].replace("-", "").upper()
            if len(code) != 52:
                raise ValueError
            wrapping_key = base64.b32decode(code + "=" * ((-len(code)) % 8))
            if len(wrapping_key) != KEY_BYTES:
                raise ValueError
        except (ValueError, TypeError) as exc:
            raise InvalidCredentials("Проверьте код восстановления.") from exc
        field = "recovery_wrapped_key"
    else:
        wrapping_key = derive_key(credential, data["kdf"])
        field = "wrapped_key"
    try:
        wrapped = decode(data[field], Aead.NONCE_SIZE + KEY_BYTES + Aead.MACBYTES)
        key = Aead(wrapping_key).decrypt(wrapped, aad)
    except CryptoError as exc:
        raise InvalidCredentials(
            "Неверный пароль или код; возможно повреждение ключевого файла."
        ) from exc
    return book_id, key
