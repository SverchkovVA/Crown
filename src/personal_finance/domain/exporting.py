"""Формат управляемого экспорта журнала, без файловой системы и сети."""

import csv
import io
import json
from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum
from typing import Any

from personal_finance.domain.errors import ValidationError
from personal_finance.domain.ledger import (
    OPERATION_LABELS,
    Operation,
    OperationDraft,
    OperationKind,
    require_identifier,
)
from personal_finance.domain.query import OperationQuery


class ExportFormat(StrEnum):
    JSON = "json"
    CSV = "csv"


@dataclass(frozen=True, slots=True)
class ExportSettings:
    query: OperationQuery = OperationQuery()
    include_notes: bool = False
    include_tags: bool = False
    include_names: bool = True

    def __post_init__(self) -> None:
        if (
            not isinstance(self.query, OperationQuery)
            or type(self.include_notes) is not bool
            or type(self.include_tags) is not bool
            or type(self.include_names) is not bool
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
class ExportLabels:
    accounts: tuple[tuple[str, str], ...] = ()
    banks: tuple[tuple[str, str], ...] = ()
    categories: tuple[tuple[str, str], ...] = ()
    tags: tuple[tuple[str, str], ...] = ()

    def __post_init__(self) -> None:
        for group in (self.accounts, self.banks, self.categories, self.tags):
            if not isinstance(group, tuple):
                raise ValidationError("Названия экспорта должны быть неизменяемым снимком.")
            seen: set[str] = set()
            for pair in group:
                if not isinstance(pair, tuple) or len(pair) != 2:
                    raise ValidationError("Некорректное название в экспорте.")
                identifier, name = pair
                require_identifier(identifier)
                if (
                    identifier in seen
                    or not isinstance(name, str)
                    or len(name) > 16384
                    or any(ord(c) < 32 for c in name)
                ):
                    raise ValidationError("Некорректное или повторное название в экспорте.")
                seen.add(identifier)


@dataclass(frozen=True, slots=True)
class ExportPreview:
    """Неизменяемая выборка. Сериализация использует тот же проверенный снимок."""

    operations: tuple[Operation, ...]
    settings: ExportSettings
    labels: ExportLabels = ExportLabels()
    source_book_id: str | None = None

    def __post_init__(self) -> None:
        _validate_operations(self.operations)
        if not isinstance(self.settings, ExportSettings) or not isinstance(
            self.labels, ExportLabels
        ):
            raise ValidationError("Некорректный состав экспорта.")
        if self.source_book_id is not None:
            require_identifier(self.source_book_id)

    def rows(self) -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = []
        accounts, banks = dict(self.labels.accounts), dict(self.labels.banks)
        categories, tags = dict(self.labels.categories), dict(self.labels.tags)
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
            if self.settings.include_names:
                value.update(
                    {
                        "account_name": accounts.get(draft.account_id, ""),
                        "bank_name": banks.get(draft.account_id, ""),
                        "destination_account_name": accounts.get(
                            draft.destination_account_id or "", ""
                        ),
                        "destination_bank_name": banks.get(draft.destination_account_id or "", ""),
                        "category_name": categories.get(draft.category_id or "", ""),
                        "fee_category_name": categories.get(draft.fee_category_id or "", ""),
                    }
                )
                for part in value["parts"]:
                    part["category_name"] = categories.get(part["category_id"], "")
            if self.settings.include_notes:
                value["note"] = draft.note
            if self.settings.include_tags:
                value["tag_ids"] = list(draft.tag_ids)
                if self.settings.include_names:
                    value["tag_names"] = [tags.get(identifier, "") for identifier in draft.tag_ids]
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
    headers = ["date", "kind"]
    if preview.settings.include_names:
        headers.extend(
            ["account_name", "bank_name", "destination_account_name", "destination_bank_name"]
        )
    headers.extend(["amount", "currency", "fee"])
    if preview.settings.include_names:
        headers.extend(["category_name", "fee_category_name"])
    if preview.settings.include_notes:
        headers.append("note")
    if preview.settings.include_tags and preview.settings.include_names:
        headers.append("tag_names_json")
    headers.extend(
        [
            "amount_minor",
            "fee_minor",
            "currency_exponent",
            "operation_id",
            "revision_id",
            "account_id",
            "destination_account_id",
            "category_id",
            "fee_category_id",
            "parts_json",
            "refund_of",
            "refund_of_revision_id",
            "is_deleted",
        ]
    )
    if preview.settings.include_tags:
        headers.append("tag_ids_json")
    writer = csv.DictWriter(output, fieldnames=headers, delimiter=";", lineterminator="\r\n")
    writer.writerow({key: CSV_COLUMNS[key] for key in headers})
    for row in rows:
        row["amount"] = decimal_minor(int(row["amount_minor"]), row["currency_exponent"])
        row["fee"] = decimal_minor(int(row["fee_minor"]), row["currency_exponent"])
        row["kind"] = OPERATION_LABELS[OperationKind(row["kind"])]
        row["parts_json"] = json.dumps(row.pop("parts"), ensure_ascii=False, separators=(",", ":"))
        row["is_deleted"] = "1" if row["is_deleted"] else "0"
        for key in (
            "note",
            "account_name",
            "bank_name",
            "destination_account_name",
            "destination_bank_name",
            "category_name",
            "fee_category_name",
        ):
            if key in row:
                row[key] = _csv_note(row[key])
        for key in ("tag_ids", "tag_names"):
            if key in row:
                row[key + "_json"] = json.dumps(
                    row.pop(key), ensure_ascii=False, separators=(",", ":")
                )
        writer.writerow(row)
    return output.getvalue().encode("utf-8-sig")


def decimal_minor(minor: int, exponent: int) -> str:
    whole, fraction = divmod(abs(minor), 10**exponent)
    value = ("-" if minor < 0 else "") + str(whole)
    return value + ("," + str(fraction).zfill(exponent) if exponent else "")


CSV_COLUMNS = {
    "date": "Дата",
    "kind": "Вид операции",
    "account_name": "Счёт",
    "bank_name": "Банк",
    "destination_account_name": "Счёт получения",
    "destination_bank_name": "Банк получения",
    "amount": "Сумма",
    "currency": "Валюта",
    "fee": "Комиссия",
    "category_name": "Категория",
    "fee_category_name": "Категория комиссии",
    "note": "Примечание",
    "tag_names_json": "Теги JSON",
    "amount_minor": "Сумма, мин. ед.",
    "fee_minor": "Комиссия, мин. ед.",
    "currency_exponent": "Дробных знаков валюты",
    "operation_id": "ID операции",
    "revision_id": "ID версии",
    "account_id": "ID счёта",
    "destination_account_id": "ID счёта получения",
    "category_id": "ID категории",
    "fee_category_id": "ID категории комиссии",
    "parts_json": "Части JSON",
    "refund_of": "ID покупки возврата",
    "refund_of_revision_id": "ID версии покупки",
    "is_deleted": "Удалена",
    "tag_ids_json": "ID тегов JSON",
}
