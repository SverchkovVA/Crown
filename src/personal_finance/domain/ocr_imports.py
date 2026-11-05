"""Проверенное происхождение OCR и явная финансовая команда; без SQL/Qt."""

import json
from dataclasses import dataclass, field
from datetime import date, time
from decimal import Decimal
from enum import StrEnum
from typing import Any

from personal_finance.domain.errors import ValidationError
from personal_finance.domain.imports import account_movement, digest_json
from personal_finance.domain.ledger import (
    Operation,
    OperationDraft,
    OperationKind,
    require_identifier,
)
from personal_finance.domain.models import RUB, Money
from personal_finance.domain.ocr import OcrBox, OcrDocument, OcrEngineInfo, OcrWord, require_sha256
from personal_finance.domain.ocr_cards import (
    CardCorrections,
    CashDirection,
    OcrCardDraft,
    OcrField,
    OcrIssue,
    ReviewedOcrCard,
    review_card,
)
from personal_finance.domain.ocr_profiles import PROFILE_PREFIXES

MAX_OCR_EVIDENCE_BYTES = 128 * 1024
_FIELDS = ("amount", "currency", "direction", "occurred_on", "local_time", "description")
_CONFIRMED = frozenset({"amount", "currency", "direction", "occurred_on"})
_ERROR = "Некорректное происхождение или подтверждение OCR."


def canonical(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _value(value: object) -> object:
    return value.isoformat() if isinstance(value, (date, time)) else value


def _reviewed_payload(value: ReviewedOcrCard) -> dict[str, object]:
    return {
        "minor": value.amount.minor,
        "currency": value.amount.currency.code,
        "direction": value.direction.value,
        "occurred_on": value.occurred_on.isoformat(),
        "local_time": value.local_time.isoformat() if value.local_time else None,
        "description": value.description,
    }


def _decode(payload: str) -> tuple[dict[str, Any], ReviewedOcrCard]:
    if not isinstance(payload, str) or len(payload.encode("utf-8")) > MAX_OCR_EVIDENCE_BYTES:
        raise ValidationError(_ERROR)
    data = json.loads(payload)
    if (
        not isinstance(data, dict)
        or set(data)
        != {
            "version",
            "file_sha256",
            "image_sha256",
            "document_sha256",
            "width",
            "height",
            "engine",
            "bank",
            "adapter",
            "fields",
            "words",
            "reviewed",
            "changed_fields",
        }
        or type(data["version"]) is not int
        or data["version"] != 1
    ):
        raise ValidationError(_ERROR)
    for key in ("file_sha256", "image_sha256", "document_sha256"):
        require_sha256(data[key])
    if (
        not isinstance(data["bank"], str)
        or data["bank"] not in PROFILE_PREFIXES
        or canonical(data) != payload
    ):
        raise ValidationError(_ERROR)
    prefix = PROFILE_PREFIXES[data["bank"]]
    if not isinstance(data["adapter"], str) or not data["adapter"].startswith(prefix):
        raise ValidationError(_ERROR)
    engine = data["engine"]
    if not isinstance(engine, dict) or set(engine) != {"name", "version", "models"}:
        raise ValidationError(_ERROR)
    models = engine["models"]
    if not isinstance(models, list) or any(
        not isinstance(pair, list) or len(pair) != 2 for pair in models
    ):
        raise ValidationError(_ERROR)
    info = OcrEngineInfo(engine["name"], engine["version"], tuple(tuple(v) for v in models))
    values = data["words"]
    if not isinstance(values, list) or not 1 <= len(values) <= 10000:
        raise ValidationError(_ERROR)
    words: list[OcrWord] = []
    lookup: dict[int, int] = {}
    for entry in values:
        if not isinstance(entry, dict) or set(entry) != {
            "index",
            "text",
            "box",
            "confidence",
            "line",
            "number",
        }:
            raise ValidationError(_ERROR)
        if not isinstance(entry["confidence"], str):
            raise ValidationError(_ERROR)
        index = entry["index"]
        if type(index) is not int or not 0 <= index < 10000 or index in lookup:
            raise ValidationError(_ERROR)
        lookup[index] = len(words)
        words.append(
            OcrWord(
                entry["text"],
                OcrBox(*entry["box"]),
                Decimal(entry["confidence"]),
                tuple(entry["line"]),
                entry["number"],
            )
        )
    if list(lookup) != sorted(lookup):
        raise ValidationError(_ERROR)
    document = OcrDocument(data["image_sha256"], data["width"], data["height"], info, tuple(words))
    fields = data["fields"]
    if not isinstance(fields, dict) or set(fields) != set(_FIELDS):
        raise ValidationError(_ERROR)
    decoded = {}
    for name in _FIELDS:
        part = fields[name]
        if not isinstance(part, dict) or set(part) != {"value", "words", "issues"}:
            raise ValidationError(_ERROR)
        if not isinstance(part["issues"], list):
            raise ValidationError(_ERROR)
        refs = part["words"]
        if not isinstance(refs, list) or any(type(i) is not int or i not in lookup for i in refs):
            raise ValidationError(_ERROR)
        value = part["value"]
        if value is not None:
            if name == "direction":
                value = CashDirection(value)
            elif name == "occurred_on":
                value = date.fromisoformat(value)
            elif name == "local_time":
                value = time.fromisoformat(value)
        decoded[name] = OcrField(
            value, tuple(lookup[i] for i in refs), tuple(OcrIssue(i) for i in part["issues"])
        )
    referenced = {index for part in fields.values() for index in part["words"]}
    if set(lookup) != referenced:
        raise ValidationError(_ERROR)
    if not fields["amount"]["words"]:
        raise ValidationError("Для финансового подтверждения нужна область исходной суммы.")
    source = OcrCardDraft(
        document,
        data["adapter"],
        decoded["amount"],
        decoded["currency"],
        decoded["direction"],
        decoded["occurred_on"],
        decoded["local_time"],
        decoded["description"],
    )
    corrected = data["reviewed"]
    if not isinstance(corrected, dict) or set(corrected) != {
        "minor",
        "currency",
        "direction",
        "occurred_on",
        "local_time",
        "description",
    }:
        raise ValidationError(_ERROR)
    minor = corrected["minor"]
    amount = Money(minor)
    local_time = (
        time.fromisoformat(corrected["local_time"]) if corrected["local_time"] is not None else None
    )
    if local_time is not None and (
        local_time.tzinfo is not None or local_time.second or local_time.microsecond
    ):
        raise ValidationError(_ERROR)
    replay = review_card(
        source,
        CardCorrections(
            f"{amount.minor // 100},{amount.minor % 100:02d}",
            corrected["currency"],
            CashDirection(corrected["direction"]),
            date.fromisoformat(corrected["occurred_on"]).strftime("%d.%m.%Y"),
            local_time.strftime("%H:%M") if local_time else "",
            corrected["description"],
        ),
        confirmed_fields=_CONFIRMED,
        single_completed_operation=True,
    )
    if (
        _reviewed_payload(replay) != corrected
        or list(replay.changed_fields) != data["changed_fields"]
    ):
        raise ValidationError(_ERROR)
    return data, replay


@dataclass(frozen=True, slots=True)
class OcrEvidence:
    payload: str = field(repr=False)

    def __post_init__(self) -> None:
        try:
            _decode(self.payload)
        except (
            ValueError,
            TypeError,
            KeyError,
            AttributeError,
            ArithmeticError,
            IndexError,
            RecursionError,
        ):
            raise ValidationError(_ERROR) from None

    @property
    def data(self) -> dict[str, Any]:
        return json.loads(self.payload)  # Новый снимок, внутреннее состояние неизменяемо.

    @property
    def reviewed(self) -> ReviewedOcrCard:
        return _decode(self.payload)[1]

    @property
    def source_key(self) -> str:
        data = self.data
        return digest_json(
            ["ocr-row-v1", data["image_sha256"], sorted(data["fields"]["amount"]["words"])]
        )

    @property
    def content_hash(self) -> str:
        data = self.data
        del data["file_sha256"]  # Метаданные JPEG не создают новую финансовую строку.
        return digest_json(data)


def make_ocr_evidence(file_sha256: str, bank: str, reviewed: ReviewedOcrCard) -> OcrEvidence:
    if (
        not isinstance(reviewed, ReviewedOcrCard)
        or not isinstance(reviewed.source, OcrCardDraft)
        or reviewed.source.blocked_reason
    ):
        raise ValidationError(_ERROR)
    try:
        doc, card = reviewed.source.document, reviewed.source

        def word(index: int, w: OcrWord) -> dict[str, object]:
            return {
                "index": index,
                "text": w.text,
                "box": [w.box.left, w.box.top, w.box.width, w.box.height],
                "confidence": str(w.confidence),
                "line": list(w.line_key),
                "number": w.word_number,
            }

        indices = sorted({i for value in card.fields() for i in value.word_indices})
        engine = {
            "name": doc.engine.name,
            "version": doc.engine.version,
            "models": [list(v) for v in doc.engine.model_sha256],
        }
        data = {
            "version": 1,
            "file_sha256": file_sha256,
            "image_sha256": doc.image_sha256,
            "document_sha256": digest_json(
                {
                    "engine": engine,
                    "width": doc.width,
                    "height": doc.height,
                    "words": [word(i, w) for i, w in enumerate(doc.words)],
                }
            ),
            "width": doc.width,
            "height": doc.height,
            "engine": engine,
            "bank": bank,
            "adapter": card.adapter,
            "fields": {
                name: {
                    "value": _value(getattr(card, name).value),
                    "words": list(getattr(card, name).word_indices),
                    "issues": [v.value for v in getattr(card, name).issues],
                }
                for name in _FIELDS
            },
            "words": [word(i, doc.words[i]) for i in indices],
            "reviewed": _reviewed_payload(reviewed),
            "changed_fields": list(reviewed.changed_fields),
        }
        return OcrEvidence(canonical(data))
    except (ValueError, TypeError, KeyError, AttributeError, ArithmeticError, RecursionError):
        raise ValidationError(_ERROR) from None


class OcrImportStatus(StrEnum):
    NEW = "new"
    POSSIBLE = "possible"
    DUPLICATE = "duplicate"
    CONFLICT = "conflict"


@dataclass(frozen=True, slots=True)
class OcrSelection:
    evidence: OcrEvidence = field(repr=False)
    account_id: str
    draft: OperationDraft = field(repr=False)
    acknowledged_duplicate: bool = False
    existing_operation_id: str | None = None
    expected_revision_id: str | None = None

    def __post_init__(self) -> None:
        if (
            not isinstance(self.evidence, OcrEvidence)
            or not isinstance(self.draft, OperationDraft)
            or self.draft.kind == OperationKind.OPENING
            or type(self.acknowledged_duplicate) is not bool
        ):
            raise ValidationError("Некорректная финансовая команда OCR.")
        require_identifier(self.account_id)
        if (self.existing_operation_id is None) != (self.expected_revision_id is None):
            raise ValidationError("Для связи нужна текущая версия операции.")
        for value in (self.existing_operation_id, self.expected_revision_id):
            if value is not None:
                require_identifier(value)
        checked = self.evidence.reviewed
        expected = checked.amount.minor * (1 if checked.direction == CashDirection.IN else -1)
        if (
            self.draft.amount.currency != RUB
            or self.draft.occurred_on != checked.occurred_on
            or account_movement(self.draft, self.account_id) != expected
        ):
            raise ValidationError(
                "Операция должна точно сохранять проверенные дату, валюту и движение счёта OCR."
            )


@dataclass(frozen=True, slots=True)
class OcrImportPreview:
    selection: OcrSelection = field(repr=False)
    status: OcrImportStatus
    candidates: tuple[Operation, ...] = field(default=(), repr=False)
    message: str = ""


@dataclass(frozen=True, slots=True)
class OcrImportResult:
    audit_id: str
    operation: Operation = field(repr=False)
    linked: bool
