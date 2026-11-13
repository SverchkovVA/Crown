"""Локальный снимок справочников с номерами, действующими внутри файла."""

import csv
import io
import json
from collections.abc import Sequence
from dataclasses import asdict, dataclass
from enum import StrEnum

from personal_finance.domain.exporting import ExportFormat, csv_safe_text
from personal_finance.domain.models import Account, Category, Institution


class CatalogSection(StrEnum):
    BANKS = "banks"
    ACCOUNTS = "accounts"
    CATEGORIES = "categories"


SECTION_LABELS = {
    CatalogSection.BANKS: "Банки",
    CatalogSection.ACCOUNTS: "Счета",
    CatalogSection.CATEGORIES: "Категории",
}
FIELDS = ["Раздел", "Номер в файле", "Название", "Тип", "Валюта", "Связь в файле"]


@dataclass(frozen=True, slots=True)
class CatalogExportRow:
    section: CatalogSection
    number: str
    name: str | None = None
    kind: str | None = None
    currency: str | None = None
    related: str | None = None


@dataclass(frozen=True, slots=True)
class CatalogExport:
    sections: tuple[CatalogSection, ...]
    rows: tuple[CatalogExportRow, ...]


def prepare_catalog_export(
    banks: Sequence[Institution],
    accounts: Sequence[Account],
    categories: Sequence[Category],
    sections: frozenset[CatalogSection],
    *,
    include_names: bool,
) -> CatalogExport:
    rows: list[CatalogExportRow] = []
    bank_numbers = (
        {bank.id: f"Б{number}" for number, bank in enumerate(banks, 1)}
        if CatalogSection.BANKS in sections
        else {}
    )
    if CatalogSection.BANKS in sections:
        rows.extend(
            CatalogExportRow(
                CatalogSection.BANKS, bank_numbers[bank.id], bank.name if include_names else None
            )
            for bank in banks
        )
    if CatalogSection.ACCOUNTS in sections:
        rows.extend(
            CatalogExportRow(
                CatalogSection.ACCOUNTS,
                f"С{number}",
                account.name if include_names else None,
                account.kind.value,
                account.currency,
                bank_numbers.get(account.institution_id) if account.institution_id else None,
            )
            for number, account in enumerate(accounts, 1)
        )
    if CatalogSection.CATEGORIES in sections:
        numbers = {category.id: f"К{number}" for number, category in enumerate(categories, 1)}
        rows.extend(
            CatalogExportRow(
                CatalogSection.CATEGORIES,
                numbers[category.id],
                category.name if include_names else None,
                category.kind.value,
                related=numbers.get(category.parent_id) if category.parent_id else None,
            )
            for category in categories
        )
    return CatalogExport(
        tuple(section for section in CatalogSection if section in sections), tuple(rows)
    )


def catalog_export_cells(row: CatalogExportRow) -> list[str]:
    return [
        SECTION_LABELS[row.section],
        row.number,
        row.name or "",
        row.kind or "",
        row.currency or "",
        row.related or "",
    ]


def serialize_catalog_export(snapshot: CatalogExport, format: ExportFormat) -> bytes:
    if format is ExportFormat.JSON:
        payload = {
            "format": "crown-catalogs",
            "version": 1,
            "sections": list(snapshot.sections),
            "rows": [
                {key: value for key, value in asdict(row).items() if value is not None}
                for row in snapshot.rows
            ],
        }
        return (
            json.dumps(payload, ensure_ascii=False, indent=2, allow_nan=False).encode("utf-8")
            + b"\n"
        )
    target = io.StringIO(newline="")
    writer = csv.writer(target, delimiter=";", lineterminator="\r\n")
    writer.writerow(FIELDS)
    for row in snapshot.rows:
        values = catalog_export_cells(row)
        values[2] = csv_safe_text(values[2])
        writer.writerow(values)
    return target.getvalue().encode("utf-8-sig")
