"""Средства на счетах на дату по принятым проводкам, отдельно по валютам."""

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date

from personal_finance.domain.analytics import ReportPeriod
from personal_finance.domain.analytics_cashflow import CashMovement, cash_flow_report
from personal_finance.domain.errors import ValidationError
from personal_finance.domain.ledger import Operation, require_identifier
from personal_finance.domain.models import RUB, Account, AccountKind, Currency


@dataclass(frozen=True, slots=True)
class AccountMoneyPosition:
    account: Account
    currency: Currency
    minor: int


@dataclass(frozen=True, slots=True)
class PositionTotals:
    currency: Currency
    bank_and_cash_minor: int
    investment_cash_minor: int

    @property
    def total_minor(self) -> int:
        return self.bank_and_cash_minor + self.investment_cash_minor


@dataclass(frozen=True, slots=True)
class AccountPosition:
    as_of: date
    accounts: tuple[AccountMoneyPosition, ...]
    totals: tuple[PositionTotals, ...]
    sources: tuple[CashMovement, ...]


def account_position(
    operations: Sequence[Operation],
    accounts: Sequence[Account],
    as_of: date,
    *,
    currencies: Sequence[Currency] = (RUB,),
) -> AccountPosition:
    report = cash_flow_report(operations, ReportPeriod(as_of, as_of))
    descriptions: dict[str, Currency] = {}
    for currency in (*currencies, *(total.currency for total in report.totals)):
        if not isinstance(currency, Currency):
            raise ValidationError("Укажите описание валюты счёта.")
        if currency.code in descriptions and descriptions[currency.code] != currency:
            raise ValidationError("Определения одной валюты различаются.")
        descriptions[currency.code] = currency
    records: dict[str, Account] = {}
    for account in accounts:
        if not isinstance(account, Account) or not isinstance(account.kind, AccountKind):
            raise ValidationError("Укажите действующие счета для отчёта.")
        require_identifier(account.id)
        if account.id in records:
            raise ValidationError("Передайте каждый счёт один раз.")
        if account.currency not in descriptions:
            raise ValidationError("Не задано описание валюты счёта.")
        records[account.id] = account
    balances = {value.account_id: value.totals for value in report.accounts}
    if set(balances) - records.keys():
        raise ValidationError("В движении средств найден неизвестный счёт.")
    rows = []
    totals: dict[Currency, list[int]] = {}
    for account in sorted(records.values(), key=lambda a: (a.name.casefold(), a.id)):
        currency = descriptions[account.currency]
        balance = balances.get(account.id)
        if balance is not None and balance.currency != currency:
            raise ValidationError("Валюта движения отличается от валюты счёта.")
        minor = balance.closing_minor if balance is not None else 0
        rows.append(AccountMoneyPosition(account, currency, minor))
        index = 1 if account.kind == AccountKind.INVESTMENT else 0
        totals.setdefault(currency, [0, 0])[index] += minor
    return AccountPosition(
        as_of,
        tuple(rows),
        tuple(
            PositionTotals(currency, *totals[currency])
            for currency in sorted(totals, key=lambda c: c.code)
        ),
        report.movements,
    )
