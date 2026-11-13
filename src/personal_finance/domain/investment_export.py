"""Экспорт видимого снимка инвестиционных позиций."""

import csv
import io
import json
from dataclasses import dataclass
from datetime import date

from personal_finance.domain.exporting import ExportFormat, csv_safe_text, decimal_minor
from personal_finance.domain.investment_portfolio import PortfolioRow, SavedPortfolioReport
from personal_finance.domain.investments import SecurityKind
from personal_finance.domain.models import Currency


@dataclass(frozen=True, slots=True)
class PortfolioExportRow:
    security_code: str
    security_kind: SecurityKind
    account_name: str | None
    currency: Currency
    quantity: int
    cost_minor: int
    market_minor: int | None
    realized_minor: int
    payouts_minor: int
    result_minor: int | None
    price_date: date | None


@dataclass(frozen=True, slots=True)
class PortfolioExport:
    as_of: date
    rows: tuple[PortfolioExportRow, ...]
    include_names: bool
    unavailable_count: int


def portfolio_export_row(row: PortfolioRow, *, include_names: bool) -> PortfolioExportRow:
    return PortfolioExportRow(
        security_code=row.position.security.code,
        security_kind=row.position.security.kind,
        account_name=row.position.account.name if include_names else None,
        currency=row.position.security.currency,
        quantity=row.position.quantity,
        cost_minor=row.position.cost_minor,
        market_minor=row.market_minor,
        realized_minor=row.position.realized_minor,
        payouts_minor=row.position.distributions_minor,
        result_minor=row.result.total_minor if row.result else None,
        price_date=row.price_date,
    )


def prepare_portfolio_export(
    report: SavedPortfolioReport, *, include_names: bool
) -> PortfolioExport:
    rows = tuple(portfolio_export_row(row, include_names=include_names) for row in report.rows)
    return PortfolioExport(report.as_of, rows, include_names, report.unavailable_count)


def serialize_portfolio_export(preview: PortfolioExport, format: ExportFormat) -> bytes:
    if format is ExportFormat.JSON:
        rows = [
            {
                "instrument": row.security_code,
                "kind": row.security_kind.value,
                "currency": row.currency.code,
                "currency_exponent": row.currency.exponent,
                "quantity": str(row.quantity),
                "cost_minor": str(row.cost_minor),
                "market_minor": str(row.market_minor) if row.market_minor is not None else None,
                "realized_minor": str(row.realized_minor),
                "payouts_minor": str(row.payouts_minor),
                "result_minor": str(row.result_minor) if row.result_minor is not None else None,
                "price_date": row.price_date.isoformat() if row.price_date else None,
                **({"account_name": row.account_name} if preview.include_names else {}),
            }
            for row in preview.rows
        ]
        return (
            json.dumps(
                {
                    "format": "crown-investment-portfolio",
                    "version": 1,
                    "as_of": preview.as_of.isoformat(),
                    "unavailable_positions": preview.unavailable_count,
                    "money_encoding": "integer_minor_decimal_string",
                    "positions": rows,
                },
                ensure_ascii=False,
                indent=2,
                allow_nan=False,
            ).encode("utf-8")
            + b"\n"
        )

    target = io.StringIO(newline="")
    writer = csv.writer(target, delimiter=";", lineterminator="\r\n")
    headers = ["Дата отчёта", "Инструмент", "Тип"]
    if preview.include_names:
        headers.append("Счёт")
    writer.writerow(
        [
            *headers,
            "Валюта",
            "Бумаг",
            "Себестоимость",
            "Оценка",
            "Реализация",
            "Выплаты",
            "Результат",
            "Дата цены",
        ]
    )
    kinds = {SecurityKind.STOCK: "Акция", SecurityKind.BOND: "Облигация", SecurityKind.FUND: "БПИФ"}
    for row in preview.rows:
        prefix = [
            preview.as_of.isoformat(),
            csv_safe_text(row.security_code),
            kinds[row.security_kind],
        ]
        if preview.include_names:
            prefix.append(csv_safe_text(row.account_name or ""))
        amounts = [
            "" if minor is None else decimal_minor(minor, row.currency.exponent)
            for minor in (
                row.cost_minor,
                row.market_minor,
                row.realized_minor,
                row.payouts_minor,
                row.result_minor,
            )
        ]
        writer.writerow(
            [
                *prefix,
                row.currency.code,
                str(row.quantity),
                *amounts,
                row.price_date.isoformat() if row.price_date else "",
            ]
        )
    return target.getvalue().encode("utf-8-sig")
