"""Сводка денег по журналу и оценённых бумаг на одну дату."""

from collections.abc import Sequence
from dataclasses import dataclass

from personal_finance.domain.errors import ValidationError
from personal_finance.domain.investment_portfolio import SavedPortfolioReport
from personal_finance.domain.ledger import AccountBalance
from personal_finance.domain.models import Account, AccountKind, Currency


@dataclass(frozen=True, slots=True)
class FinancialAssetTotal:
    currency: Currency
    liquid_minor: int
    investment_cash_minor: int
    securities_minor: int | None
    unpriced_count: int
    total_minor: int | None


def summarize_financial_assets(
    accounts: Sequence[Account],
    cash: Sequence[AccountBalance],
    portfolio: SavedPortfolioReport,
) -> tuple[FinancialAssetTotal, ...]:
    by_account = {item.id: item for item in accounts}
    balances: dict[Currency, list[int]] = {}
    seen: set[str] = set()
    definitions: dict[str, Currency] = {}
    for row in cash:
        account = by_account.get(row.account_id)
        currency = row.amount.currency
        if account is None or account.currency != currency.code or row.account_id in seen:
            raise ValidationError("Денежный остаток не соответствует одному счёту книги.")
        if definitions.setdefault(currency.code, currency) != currency:
            raise ValidationError("Одинаковый код валюты указан с разной точностью.")
        seen.add(row.account_id)
        amounts = balances.setdefault(currency, [0, 0])
        index = 1 if account.kind is AccountKind.INVESTMENT else 0
        amounts[index] += row.amount.minor

    securities = {row.currency: row for row in portfolio.totals}
    for currency in securities:
        if definitions.setdefault(currency.code, currency) != currency:
            raise ValidationError("Одинаковый код валюты указан с разной точностью.")

    result = []
    for currency in sorted(balances.keys() | securities.keys(), key=lambda item: item.code):
        liquid, brokerage = balances.get(currency, [0, 0])
        portfolio_total = securities.get(currency)
        valued = portfolio_total.priced_market_minor if portfolio_total is not None else 0
        missing = portfolio_total.unpriced_count if portfolio_total is not None else 0
        total = (
            None
            if missing or portfolio.unavailable_count or valued is None
            else liquid + brokerage + valued
        )
        result.append(FinancialAssetTotal(currency, liquid, brokerage, valued, missing, total))
    return tuple(result)
