from dataclasses import replace
from datetime import date
from uuid import uuid4

import pytest

from personal_finance.domain.account_position import account_position
from personal_finance.domain.errors import ValidationError
from personal_finance.domain.ledger import OperationKind
from personal_finance.domain.models import MAX_MINOR, RUB, Account, AccountKind, Currency, Money
from tests.unit.test_analytics import A, B, operation
from tests.unit.test_analytics_cashflow import snapshot

AS_OF = date(2026, 10, 31)


def account(identifier, kind=AccountKind.DEBIT, currency="RUB"):
    return Account(identifier, "Синтетический счёт " + identifier, kind, None, currency)


def test_position_groups_cash_and_investment_money_with_immutable_sources():
    zero = account(str(uuid4()), AccountKind.SAVINGS)
    negative = account(str(uuid4()), AccountKind.CASH)
    values = snapshot()
    expense = operation(OperationKind.EXPENSE, 2000)
    expense = replace(expense, draft=replace(expense.draft, account_id=negative.id))
    future = operation(OperationKind.INCOME, 500000, day=date(2026, 11, 1))
    deleted = replace(operation(OperationKind.INCOME, 500000), is_deleted=True)
    result = account_position(
        [*values, expense, future, deleted],
        [account(B, AccountKind.INVESTMENT), zero, negative, account(A)],
        AS_OF,
    )
    assert result.as_of == AS_OF
    assert {row.account.id: row.minor for row in result.accounts} == {
        A: 99900,
        B: 45000,
        zero.id: 0,
        negative.id: -2000,
    }
    assert len(result.totals) == 1
    totals = result.totals[0]
    assert (totals.bank_and_cash_minor, totals.investment_cash_minor, totals.total_minor) == (
        97900,
        45000,
        142900,
    )
    assert {line.operation.revision_id for line in result.sources} == {
        value.revision_id for value in [*values, expense]
    }
    assert sum(line.minor for line in result.sources) == totals.total_minor
    assert all(
        line.operation is next(v for v in [*values, expense] if v.id == line.operation.id)
        for line in result.sources
    )


def test_position_keeps_currencies_separate_and_accepts_explicit_empty_account_currency():
    usd, jpy = Currency("USD", 2), Currency("JPY", 0)
    income = operation(OperationKind.INCOME, 150, currency=usd, fee=Money(0, usd))
    result = account_position(
        [income],
        [account(A, currency="USD"), account(B, currency="JPY")],
        AS_OF,
        currencies=(RUB, jpy),
    )
    assert [(row.currency, row.total_minor) for row in result.totals] == [(jpy, 0), (usd, 150)]
    assert result.accounts[0].currency.code in {"USD", "JPY"}
    assert account_position([], [], AS_OF).totals == ()


def test_position_uses_exact_aggregate_above_single_operation_limit():
    other = operation(OperationKind.INCOME, MAX_MINOR)
    other = replace(other, draft=replace(other.draft, account_id=B))
    result = account_position(
        [operation(OperationKind.INCOME, MAX_MINOR), other],
        [account(A), account(B, AccountKind.INVESTMENT)],
        AS_OF,
    )
    assert result.totals[0].total_minor == MAX_MINOR * 2


def test_position_rejects_inconsistent_catalog_and_currency_definitions():
    values = [operation(OperationKind.INCOME, 100)]
    for records, message in [
        ([account(A), account(A)], "один раз"),
        ([], "неизвестный счёт"),
        ([account(A, currency="USD")], "описание валюты"),
    ]:
        with pytest.raises(ValidationError, match=message):
            account_position(values, records, AS_OF)
    usd = Currency("USD", 2)
    with pytest.raises(ValidationError, match="Валюта движения"):
        account_position(values, [account(A, currency="USD")], AS_OF, currencies=(RUB, usd))
    with pytest.raises(ValidationError, match="Определения одной валюты"):
        account_position(values, [account(A)], AS_OF, currencies=(Currency("RUB", 3),))
