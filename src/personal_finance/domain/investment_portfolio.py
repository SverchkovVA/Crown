"""Сводка актуальных сохранённых позиций на выбранную дату."""

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date

from personal_finance.domain.errors import ValidationError
from personal_finance.domain.investment_positions import SavedInvestmentPosition
from personal_finance.domain.investment_prices import (
    InvestmentValuation,
    PositionResult,
    position_result,
    value_investments,
)
from personal_finance.domain.investments import SecurityKind, SecurityPosition, project_investments
from personal_finance.domain.models import Account, Currency


@dataclass(frozen=True, slots=True)
class PortfolioRow:
    record: SavedInvestmentPosition
    valuation: InvestmentValuation
    position: SecurityPosition
    market_minor: int | None
    price_date: date | None
    result: PositionResult | None


@dataclass(frozen=True, slots=True)
class PortfolioCurrencyTotal:
    currency: Currency
    open_count: int
    unpriced_count: int
    cost_minor: int
    priced_market_minor: int | None
    realized_minor: int
    distributions_minor: int
    result_minor: int | None


@dataclass(frozen=True, slots=True)
class SavedPortfolioReport:
    as_of: date
    rows: tuple[PortfolioRow, ...]
    totals: tuple[PortfolioCurrencyTotal, ...]
    unavailable_count: int


@dataclass(frozen=True, slots=True)
class PortfolioAllocation:
    currency: Currency
    kind: SecurityKind
    open_count: int
    unpriced_count: int
    priced_market_minor: int | None
    share_basis_points: int | None


def portfolio_allocation(report: SavedPortfolioReport) -> tuple[PortfolioAllocation, ...]:
    totals = {total.currency: total for total in report.totals}
    groups: dict[tuple[Currency, SecurityKind], list[PortfolioRow]] = {}
    for row in report.rows:
        if row.position.quantity:
            security = row.position.security
            groups.setdefault((security.currency, security.kind), []).append(row)
    result = []
    for currency, kind in sorted(groups, key=lambda key: (key[0].code, key[1].value)):
        rows = groups[(currency, kind)]
        known = [row.market_minor for row in rows if row.market_minor is not None]
        subtotal = sum(known) if known else None
        total = totals[currency]
        share = None
        if (
            subtotal is not None
            and total.priced_market_minor
            and not total.unpriced_count
            and not report.unavailable_count
        ):
            whole, remainder = divmod(subtotal * 10000, total.priced_market_minor)
            share = whole + int(2 * remainder >= total.priced_market_minor)
        result.append(
            PortfolioAllocation(currency, kind, len(rows), len(rows) - len(known), subtotal, share)
        )
    return tuple(result)


def _currency_total(currency: Currency, rows: list[PortfolioRow]) -> PortfolioCurrencyTotal:
    opened = [row for row in rows if row.position.quantity]
    values = [row.market_minor for row in opened if row.market_minor is not None]
    unpriced_count = len(opened) - len(values)
    return PortfolioCurrencyTotal(
        currency=currency,
        open_count=len(opened),
        unpriced_count=unpriced_count,
        cost_minor=sum(row.position.cost_minor for row in opened),
        priced_market_minor=sum(values) if values or not opened else None,
        realized_minor=sum(row.position.realized_minor for row in rows),
        distributions_minor=sum(row.position.distributions_minor for row in rows),
        result_minor=(
            None
            if unpriced_count
            else sum(row.result.total_minor for row in rows if row.result is not None)
        ),
    )


def report_saved_portfolio(
    accounts: Sequence[Account], records: Sequence[SavedInvestmentPosition], as_of: date
) -> SavedPortfolioReport:
    if type(as_of) is not date:
        raise ValidationError("Укажите календарную дату портфеля.")
    account_by_id = {account.id: account for account in accounts}
    rows: list[PortfolioRow] = []
    by_currency: dict[Currency, list[PortfolioRow]] = {}
    currency_codes: dict[str, Currency] = {}
    unavailable = 0
    for record in records:
        if record.is_deleted:
            continue
        inputs = record.inputs
        account = account_by_id.get(inputs.account_id)
        if account is None:
            unavailable += 1
            continue
        currency = inputs.security.currency
        if currency_codes.setdefault(currency.code, currency) != currency:
            raise ValidationError("Одинаковый код валюты указан с разной точностью.")
        # Порядок и цена относятся к позиции счёта. Один код на двух счетах
        # может иметь одинаковые номера сделок и разные ручные цены.
        portfolio = project_investments((account,), (inputs.security,), inputs.events, as_of)
        if not portfolio.positions:
            continue
        (position,) = portfolio.positions
        valuation = value_investments(portfolio, inputs.prices)
        valued = valuation.positions[0] if valuation.positions else None
        market = valued.market_minor if valued is not None else 0
        price_date = valued.price.quoted_on if valued is not None and valued.price else None
        row = PortfolioRow(
            record, valuation, position, market, price_date, position_result(position, market)
        )
        rows.append(row)
        by_currency.setdefault(currency, []).append(row)
    rows.sort(
        key=lambda row: (
            row.position.security.currency.code,
            row.position.security.code,
            row.position.account.name.casefold(),
            row.record.id,
        )
    )
    totals = tuple(
        _currency_total(currency, by_currency[currency])
        for currency in sorted(by_currency, key=lambda value: value.code)
    )
    return SavedPortfolioReport(as_of, tuple(rows), totals, unavailable)
