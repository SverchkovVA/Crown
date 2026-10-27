"""Формат управляемого экспорта журнала, без файловой системы и сети."""

import csv
import io
import json
from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum
from typing import Any

from personal_finance.domain.errors import ValidationError
from personal_finance.domain.ledger import Operation, OperationDraft, require_identifier
from personal_finance.domain.query import OperationQuery


class ExportFormat(StrEnum):
    JSON = "json"
    CSV = "csv"


@dataclass(frozen=True, slots=True)
class ExportSettings:
    query: OperationQuery = OperationQuery()
    include_notes: bool = False
    include_tags: bool = False

    def __post_init__(self) -> None:
        if (
            not isinstance(self.query, OperationQuery)
            or type(self.include_notes) is not bool
            or type(self.include_tags) is not bool
        ):
            raise ValidationError("Некорректный состав экспорта.")


def _validate_operations(operations: tuple[Operation, ...]) -> None:
    if not isinstance(operations, tuple) or any(
        not isinstance(value, Operation)
        or not isinstance(value.draft, OperationDraft)
        or type(value.is_deleted) is not bool
        for value in operations
    ):
        raise ValidationError("Экспорт требует неизменяемый снимок текущих операций.")
    identifiers: set[str] = set()
    for value in operations:
        require_identifier(value.id)
        require_identifier(value.revision_id)
        if value.id in identifiers:
            raise ValidationError("В снимке экспорта несколько версий одной операции.")
        identifiers.add(value.id)


@dataclass(frozen=True, slots=True)
class ExportPreview:
    """Неизменяемая выборка. Сериализация использует тот же проверенный снимок."""

    operations: tuple[Operation, ...]
    settings: ExportSettings

    def __post_init__(self) -> None:
        _validate_operations(self.operations)
        if not isinstance(self.settings, ExportSettings):
            raise ValidationError("Некорректный состав экспорта.")

    def rows(self) -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = []
        for operation in self.operations:
            draft = operation.draft
            value: dict[str, Any] = {
                "operation_id": operation.id,
                "revision_id": operation.revision_id,
                "kind": draft.kind.value,
                "date": draft.occurred_on.isoformat(),
                "account_id": draft.account_id,
                "destination_account_id": draft.destination_account_id,
                "currency": draft.amount.currency.code,
                "currency_exponent": draft.amount.currency.exponent,
                # Строка сохраняет int64 в потребителях JSON без точного integer-типа.
                "amount_minor": str(draft.amount.minor),
                "fee_minor": str(draft.fee.minor),
                "category_id": draft.category_id,
                "fee_category_id": draft.fee_category_id,
                "parts": [
                    {"category_id": part.category_id, "amount_minor": str(part.amount.minor)}
                    for part in draft.parts
                ],
                "refund_of": draft.refund_of,
                "refund_of_revision_id": draft.refund_of_revision_id,
                "is_deleted": operation.is_deleted,
            }
            if self.settings.include_notes:
                value["note"] = draft.note
            if self.settings.include_tags:
                value["tag_ids"] = list(draft.tag_ids)
            rows.append(value)
        return rows


def prepare_journal_export(
    operations: tuple[Operation, ...],
    settings: ExportSettings,
    institutions: Mapping[str, str | None] | None = None,
) -> ExportPreview:
    """Ожидает по одной текущей версии; согласованный SQL-снимок обеспечит адаптер."""
    _validate_operations(operations)
    if not isinstance(settings, ExportSettings):
        raise ValidationError("Некорректный состав экспорта.")
    if settings.query.institution_ids and institutions is None:
        raise ValidationError("Для фильтра банков нужен снимок связей счетов и банков.")
    return ExportPreview(
        tuple(
            sorted(
                (
                    value
                    for value in operations
                    if settings.query.matches(value, institutions or {})
                ),
                key=lambda value: (value.draft.occurred_on, value.id),
            )
        ),
        settings,
    )


def _csv_note(value: str) -> str:
    # Только произвольный текст. Финансовые целые и даты не преобразуются в формулы.
    stripped = value.lstrip(" \t\r\n\ufeff")
    return "'" + value if stripped.startswith(("=", "+", "-", "@")) else value


def serialize_journal_export(preview: ExportPreview, format: ExportFormat) -> bytes:
    if not isinstance(preview, ExportPreview) or not isinstance(format, ExportFormat):
        raise ValidationError("Укажите проверенный снимок и формат экспорта.")
    rows = preview.rows()
    if format == ExportFormat.JSON:
        return (
            json.dumps(
                {
                    "format": "crown-journal-export",
                    "version": 1,
                    "money_encoding": "integer_minor_decimal_string",
                    "operations": rows,
                },
                ensure_ascii=False,
                indent=2,
                allow_nan=False,
            ).encode("utf-8")
            + b"\n"
        )
    output = io.StringIO(newline="")
    headers = [
        "operation_id",
        "revision_id",
        "kind",
        "date",
        "account_id",
        "destination_account_id",
        "currency",
        "currency_exponent",
        "amount_minor",
        "fee_minor",
        "category_id",
        "fee_category_id",
        "parts_json",
        "refund_of",
        "refund_of_revision_id",
        "is_deleted",
    ]
    if preview.settings.include_notes:
        headers.append("note")
    if preview.settings.include_tags:
        headers.append("tag_ids_json")
    writer = csv.DictWriter(output, fieldnames=headers, delimiter=";", lineterminator="\r\n")
    writer.writeheader()
    for row in rows:
        row["parts_json"] = json.dumps(row.pop("parts"), ensure_ascii=False, separators=(",", ":"))
        row["is_deleted"] = "1" if row["is_deleted"] else "0"
        if "note" in row:
            row["note"] = _csv_note(row["note"])
        if "tag_ids" in row:
            row["tag_ids_json"] = json.dumps(row.pop("tag_ids"), separators=(",", ":"))
        writer.writerow(row)
    return output.getvalue().encode("utf-8-sig")
