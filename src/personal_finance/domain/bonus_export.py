"""Неизменяемая локальная выгрузка бонусных записей."""

import csv
import io
import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date

from personal_finance.domain.analytics import ReportPeriod
from personal_finance.domain.bonuses import KIND_LABELS, BonusEntry, BonusKind
from personal_finance.domain.exporting import ExportFormat, csv_safe_text, decimal_minor


@dataclass(frozen=True, slots=True)
class BonusExportRow:
    day: date
    program: str
    kind: BonusKind
    units_minor: int
    category: str | None


@dataclass(frozen=True, slots=True)
class BonusExport:
    period: ReportPeriod
    rows: tuple[BonusExportRow, ...]
    include_names: bool


def prepare_bonus_export(
    period: ReportPeriod,
    entries: Sequence[BonusEntry],
    programs: Mapping[str, str],
    categories: Mapping[str, str],
    *,
    include_names: bool,
) -> BonusExport:
    filtered = sorted(
        (
            item
            for item in entries
            if not item.is_deleted and period.start <= item.draft.occurred_on <= period.end
        ),
        key=lambda item: (item.draft.occurred_on, item.id),
    )
    program_ids = sorted({item.draft.program_id for item in filtered})
    category_ids = sorted(
        {item.draft.category_id for item in filtered if item.draft.category_id is not None}
    )
    program_labels = {
        key: (programs.get(key) if include_names else None) or f"Программа {index}"
        for index, key in enumerate(program_ids, 1)
    }
    category_labels = {
        key: (categories.get(key) if include_names else None) or f"Категория {index}"
        for index, key in enumerate(category_ids, 1)
    }
    return BonusExport(
        period,
        tuple(
            BonusExportRow(
                item.draft.occurred_on,
                program_labels[item.draft.program_id],
                item.draft.kind,
                item.draft.units_minor,
                category_labels.get(item.draft.category_id) if item.draft.category_id else None,
            )
            for item in filtered
        ),
        include_names,
    )


def serialize_bonus_export(preview: BonusExport, format: ExportFormat) -> bytes:
    if format is ExportFormat.JSON:
        return (
            json.dumps(
                {
                    "format": "crown-bonus-journal",
                    "version": 1,
                    "period": {
                        "start": preview.period.start.isoformat(),
                        "end": preview.period.end.isoformat(),
                    },
                    "units_encoding": "integer_hundredths_decimal_string",
                    "includes_names": preview.include_names,
                    "entries": [
                        {
                            "date": row.day.isoformat(),
                            "program": row.program,
                            "kind": row.kind.value,
                            "units_hundredths": str(row.units_minor),
                            "category": row.category,
                        }
                        for row in preview.rows
                    ],
                },
                ensure_ascii=False,
                indent=2,
                allow_nan=False,
            ).encode("utf-8")
            + b"\n"
        )
    target = io.StringIO(newline="")
    writer = csv.writer(target, delimiter=";", lineterminator="\r\n")
    writer.writerow(["Дата бонусной записи", "Программа", "Вид", "Бонусы", "Категория"])
    for row in preview.rows:
        writer.writerow(
            [
                row.day.isoformat(),
                csv_safe_text(row.program),
                KIND_LABELS[row.kind],
                decimal_minor(row.units_minor, 2),
                csv_safe_text(row.category) if row.category is not None else "",
            ]
        )
    return target.getvalue().encode("utf-8-sig")
