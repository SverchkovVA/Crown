"""Локальный предпросмотр импорта: исходные строки отделены от финансовых команд."""

import hashlib
import json
from dataclasses import dataclass
from datetime import date
from enum import StrEnum

from personal_finance.domain.errors import ValidationError
from personal_finance.domain.ledger import (
    LedgerRole,
    Operation,
    OperationDraft,
    OperationKind,
    require_identifier,
)
from personal_finance.domain.models import Money

MAX_IMPORT_ROWS = 2000
MAX_IMPORT_BYTES = 8 * 1024 * 1024


def digest_json(value: object) -> str:
    return hashlib.sha256(
        json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def require_digest(value: str) -> None:
    if (
        not isinstance(value, str)
        or len(value) != 64
        or any(c not in "0123456789abcdef" for c in value)
    ):
        raise ValidationError("Некорректный отпечаток импорта.")


class ImportKind(StrEnum):
    CSV = "csv"
    OFX = "ofx"


@dataclass(frozen=True, slots=True)
class ImportRow:
    position: int
    occurred_on: date
    amount: Money
    note: str = ""
    external_id: str | None = None
    transfer_hint: bool = False

    def __post_init__(self) -> None:
        if type(self.position) is not int or not 1 <= self.position <= MAX_IMPORT_ROWS:
            raise ValidationError("Некорректный номер строки импорта.")
        if (
            type(self.occurred_on) is not date
            or not isinstance(self.amount, Money)
            or self.amount.minor == 0
        ):
            raise ValidationError("Укажите дату и ненулевую точную сумму строки импорта.")
        if (
            not isinstance(self.note, str)
            or len(self.note) > 500
            or any(ord(c) < 32 and c != "\n" for c in self.note)
        ):
            raise ValidationError(
                "Примечание импорта должно содержать не более 500 печатных символов."
            )
        if self.external_id is not None and (
            not isinstance(self.external_id, str)
            or not self.external_id.strip()
            or len(self.external_id) > 255
            or any(ord(c) < 32 for c in self.external_id)
        ):
            raise ValidationError("Некорректный внешний идентификатор строки.")
        if type(self.transfer_hint) is not bool:
            raise ValidationError("Некорректный признак перевода.")

    def as_dict(self) -> dict[str, object]:
        return {
            "position": self.position,
            "occurred_on": self.occurred_on.isoformat(),
            "minor": self.amount.minor,
            "currency": self.amount.currency.code,
            "exponent": self.amount.currency.exponent,
            "note": self.note,
            "external_id": self.external_id,
            "transfer_hint": self.transfer_hint,
        }

    @property
    def payload_hash(self) -> str:
        # Позиция не является банковским фактом: один FITID может прийти в другой строке.
        payload = self.as_dict()
        del payload["position"]
        return digest_json(payload)


@dataclass(frozen=True, slots=True)
class ImportIssue:
    position: int
    message: str


@dataclass(frozen=True, slots=True)
class ImportDocument:
    kind: ImportKind
    file_hash: str
    scope_hash: str
    rows: tuple[ImportRow, ...]
    issues: tuple[ImportIssue, ...] = ()
    warnings: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not isinstance(self.kind, ImportKind):
            raise ValidationError("Неизвестный формат импорта.")
        require_digest(self.file_hash)
        require_digest(self.scope_hash)
        if (
            not isinstance(self.rows, tuple)
            or not isinstance(self.issues, tuple)
            or not 1 <= len(self.rows) + len(self.issues) <= MAX_IMPORT_ROWS
        ):
            raise ValidationError("В файле должно быть от 1 до 2000 строк.")
        if any(not isinstance(r, ImportRow) for r in self.rows) or any(
            not isinstance(i, ImportIssue)
            or type(i.position) is not int
            or not 1 <= i.position <= MAX_IMPORT_ROWS
            or not isinstance(i.message, str)
            or not i.message
            or len(i.message) > 500
            for i in self.issues
        ):
            raise ValidationError("Некорректные строки предпросмотра.")
        positions = [r.position for r in self.rows] + [i.position for i in self.issues]
        if len(set(positions)) != len(positions):
            raise ValidationError("Номера строк импорта повторяются.")
        if not isinstance(self.warnings, tuple) or any(
            not isinstance(s, str) or len(s) > 500 for s in self.warnings
        ):
            raise ValidationError("Некорректные предупреждения импорта.")

    def source_key(self, row: ImportRow) -> str:
        identity = (
            ("id", row.external_id)
            if row.external_id is not None
            else ("file-row", self.file_hash, row.position)
        )
        return digest_json([self.kind.value, self.scope_hash, identity])


class ImportStatus(StrEnum):
    NEW = "new"
    POSSIBLE = "possible"
    DUPLICATE = "duplicate"
    CONFLICT = "conflict"


@dataclass(frozen=True, slots=True)
class ImportPreviewLine:
    row: ImportRow
    status: ImportStatus
    candidates: tuple[Operation, ...] = ()
    message: str = ""


@dataclass(frozen=True, slots=True)
class ImportPreview:
    document: ImportDocument
    account_id: str
    lines: tuple[ImportPreviewLine, ...]


@dataclass(frozen=True, slots=True)
class ImportSelection:
    position: int
    draft: OperationDraft
    acknowledged_duplicate: bool = False
    existing_operation_id: str | None = None
    expected_revision_id: str | None = None

    def __post_init__(self) -> None:
        if (
            type(self.position) is not int
            or not 1 <= self.position <= MAX_IMPORT_ROWS
            or not isinstance(self.draft, OperationDraft)
            or self.draft.kind == OperationKind.OPENING
            or type(self.acknowledged_duplicate) is not bool
        ):
            raise ValidationError("Некорректная выбранная строка импорта.")
        if (self.existing_operation_id is None) != (self.expected_revision_id is None):
            raise ValidationError("Для связи нужна ожидаемая версия существующей операции.")
        for value in (self.existing_operation_id, self.expected_revision_id):
            if value is not None:
                require_identifier(value)


@dataclass(frozen=True, slots=True)
class ImportResult:
    batch_id: str
    operations: tuple[Operation, ...]
    linked_count: int


def account_movement(draft: OperationDraft, account_id: str) -> int:
    return sum(
        p.amount.minor
        for p in draft.postings()
        if p.role == LedgerRole.ASSET and p.reference_id == account_id
    )
