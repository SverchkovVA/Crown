"""Версии правил собственных переводов внутри SQLCipher-книги."""

import json
from dataclasses import asdict
from threading import RLock
from uuid import uuid4

from sqlalchemy import Engine, text

from personal_finance.application.catalog import utc_now
from personal_finance.domain.errors import ValidationError, VaultError
from personal_finance.domain.imports import digest_json
from personal_finance.domain.ocr_imports import canonical
from personal_finance.domain.ocr_settings import OcrPreferences, TransferParty
from personal_finance.infrastructure.persistence.ocr_repository import SqlOcrRepository


def encode_preferences(value: OcrPreferences) -> str:
    return canonical({"version": 1, **asdict(value)})


def decode_preferences(payload: str) -> OcrPreferences:
    try:
        data = json.loads(payload)
        if (
            not isinstance(data, dict)
            or set(data)
            != {
                "version",
                "default_currency",
                "reference_year",
                "confirm_fields",
                "review_before_save",
                "transfer_parties",
            }
            or type(data["version"]) is not int
            or data.pop("version") != 1
        ):
            raise ValueError
        parties = data.pop("transfer_parties")
        if not isinstance(parties, list):
            raise ValueError
        result = OcrPreferences(**data, transfer_parties=tuple(TransferParty(**v) for v in parties))
        if encode_preferences(result) != payload or len(payload.encode("utf-8")) > 32768:
            raise ValueError
        return result
    except (ValueError, TypeError, KeyError, AttributeError, RecursionError):
        raise ValidationError("Некорректные сохранённые параметры OCR.") from None


class SqlOcrSettings:
    def __init__(self, engine: Engine, book_id: str, mutex: RLock, ocr: SqlOcrRepository) -> None:
        self._engine, self._book_id, self._mutex, self._ocr = engine, book_id, mutex, ocr

    def load(self) -> tuple[OcrPreferences | None, str | None]:
        with self._mutex, self._engine.connect() as connection:
            row = connection.execute(
                text("""SELECT v.payload,v.id FROM ocr_settings_heads h
                JOIN ocr_settings_versions v ON v.id=h.revision_id WHERE h.book_id=:book"""),
                {"book": self._book_id},
            ).first()
            return (decode_preferences(row.payload), row.id) if row else (None, None)

    def save(self, value: OcrPreferences, expected_revision_id: str | None) -> str:
        if not isinstance(value, OcrPreferences):
            raise ValidationError("Некорректные параметры OCR.")
        payload = encode_preferences(value)
        decode_preferences(payload)
        with self._mutex, self._engine.begin() as connection:
            current = connection.execute(
                text("SELECT revision_id FROM ocr_settings_heads WHERE book_id=:book"),
                {"book": self._book_id},
            ).scalar_one_or_none()
            if current != expected_revision_id:
                raise ValidationError("Параметры OCR уже изменились. Откройте настройки заново.")
            for party in value.transfer_parties:
                if party.account_id is not None:
                    self._ocr._account(connection, party.account_id)
            identifier = str(uuid4())
            connection.execute(
                text("""INSERT INTO ocr_settings_versions
                (id,book_id,parent_id,payload,payload_hash,created_at)
                VALUES (:id,:book,:parent,:payload,:hash,:at)"""),
                {
                    "id": identifier,
                    "book": self._book_id,
                    "parent": current,
                    "payload": payload,
                    "hash": digest_json(json.loads(payload)),
                    "at": utc_now(),
                },
            )
            if current is None:
                connection.execute(
                    text("INSERT INTO ocr_settings_heads(book_id,revision_id) VALUES (:book,:id)"),
                    {"book": self._book_id, "id": identifier},
                )
            else:
                connection.execute(
                    text("UPDATE ocr_settings_heads SET revision_id=:id WHERE book_id=:book"),
                    {"book": self._book_id, "id": identifier},
                )
            return identifier

    def check_integrity(self) -> None:
        with self._mutex, self._engine.connect() as connection:
            rows = list(
                connection.execute(
                    text("SELECT * FROM ocr_settings_versions WHERE book_id=:book"),
                    {"book": self._book_id},
                ).mappings()
            )
            head = connection.execute(
                text("SELECT revision_id FROM ocr_settings_heads WHERE book_id=:book"),
                {"book": self._book_id},
            ).scalar_one_or_none()
            by_id = {r["id"]: r for r in rows}
            seen: set[str] = set()
            while head is not None:
                if head in seen or head not in by_id:
                    raise VaultError("Повреждена история настроек OCR.")
                seen.add(head)
                row = by_id[head]
                try:
                    value = decode_preferences(row["payload"])
                    if row["payload_hash"] != digest_json(json.loads(row["payload"])):
                        raise ValidationError("Не совпал отпечаток настроек OCR.")
                    for party in value.transfer_parties:
                        if party.account_id is not None:
                            self._ocr._account(connection, party.account_id, active=False)
                except ValidationError:
                    raise VaultError("Проверка параметров OCR не пройдена.") from None
                head = row["parent_id"]
            if len(seen) != len(rows):
                raise VaultError("История настроек OCR неполна.")
