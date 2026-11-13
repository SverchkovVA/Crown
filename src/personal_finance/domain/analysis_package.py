"""Локальный пакет агрегатов с описанием расчётов."""

import json
from dataclasses import dataclass

from personal_finance.domain.analytics import FlowTotals, IncomeExpenseReport
from personal_finance.domain.investment_portfolio import SavedPortfolioReport


@dataclass(frozen=True, slots=True)
class AnalysisPackage:
    flow: IncomeExpenseReport
    include_categories: bool
    portfolio: SavedPortfolioReport | None = None


def _flow(totals: FlowTotals) -> dict[str, str | int]:
    return {
        "currency": totals.currency.code,
        "currency_exponent": totals.currency.exponent,
        "income_minor": str(totals.income_minor),
        "expense_minor": str(totals.expense_minor),
        "difference_minor": str(totals.difference_minor),
    }


def package_categories(flow: IncomeExpenseReport) -> tuple[tuple[str, FlowTotals], ...]:
    identifiers = sorted({item.category_id for item in flow.categories})
    aliases = {identifier: f"Категория {index}" for index, identifier in enumerate(identifiers, 1)}
    return tuple((aliases[item.category_id], item.totals) for item in flow.categories)


def serialize_analysis_package(package: AnalysisPackage) -> bytes:
    flow = package.flow
    methods = {
        "money": "Строка целых минимальных единиц. Делите на 10 в степени currency_exponent.",
        "currency": "Валюты показаны раздельно, без пересчёта.",
        "period": "Обе границы включены. month — первый день календарного месяца.",
        "income_minor": "Доходные проводки текущих принятых операций.",
        "expense_minor": "Расходы с учётом возвратов и комиссий по переводам.",
        "difference_minor": "Доходы минус расходы. Это не остаток денег на счетах.",
        "months": "Только месяцы с подходящими проводками. Крайние месяцы могут быть неполными.",
        "transfers": "Основная сумма перевода и начальные остатки в оборот не входят.",
    }
    payload: dict[str, object] = {
        "format": "crown-analysis-package",
        "version": 1,
        "period": {"start": flow.period.start.isoformat(), "end": flow.period.end.isoformat()},
        "methodology": methods,
        "totals": [_flow(item) for item in flow.totals],
        "months": [{"month": item.month.isoformat(), **_flow(item.totals)} for item in flow.months],
    }
    if package.include_categories:
        payload["categories"] = [
            {"category": alias, **_flow(totals)} for alias, totals in package_categories(flow)
        ]
        methods["categories"] = "Условные названия действуют только в пределах этого файла."
    if package.portfolio is not None:
        portfolio = package.portfolio
        payload["portfolio"] = {
            "as_of": portfolio.as_of.isoformat(),
            "unavailable_positions": portfolio.unavailable_count,
            "currencies": [
                {
                    "currency": item.currency.code,
                    "currency_exponent": item.currency.exponent,
                    "open_positions": item.open_count,
                    "unpriced_positions": item.unpriced_count,
                    "priced_market_minor": (
                        str(item.priced_market_minor)
                        if item.priced_market_minor is not None
                        else None
                    ),
                }
                for item in portfolio.totals
            ],
        }
        methods["portfolio"] = (
            "Оценка ценных бумаг на конец периода по последним ручным ценам. "
            "Позиций без цены в сумме нет. null означает отсутствие оценки."
        )
    return (json.dumps(payload, ensure_ascii=False, indent=2, allow_nan=False) + "\n").encode(
        "utf-8"
    )
