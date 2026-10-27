from datetime import date
from uuid import uuid4

import pytest
from hypothesis import given
from hypothesis import strategies as st

from personal_finance.domain.errors import ValidationError, VaultError
from personal_finance.domain.ledger import Operation, OperationDraft, OperationKind
from personal_finance.domain.models import MAX_MINOR, Currency, Money
from personal_finance.domain.reconciliation import reconcile_snapshot
from personal_finance.presentation.money import format_minor

ACCOUNT = str(uuid4())
DAY = date(2026, 1, 15)


@given(st.integers(-MAX_MINOR, MAX_MINOR), st.integers(-MAX_MINOR, MAX_MINOR))
def test_reconciliation_difference_is_exact_for_two_int64_balances(book, statement):
    operation = Operation(
        str(uuid4()), str(uuid4()), OperationDraft(OperationKind.OPENING, DAY, ACCOUNT, Money(book))
    )
    result = reconcile_snapshot(ACCOUNT, DAY, Money(book), Money(statement), [operation])
    assert result.difference_minor == statement - book
    assert result.matches == (statement == book)
    assert sum(line.change.minor for line in result.lines) == book


def test_difference_larger_than_money_range_is_formatted_exactly():
    assert format_minor(2 * MAX_MINOR, Currency("RUB", 2)) == "184 467 440 737 095 516,14 RUB"
    assert format_minor(-2 * MAX_MINOR, Currency("RUB", 2)) == "-184 467 440 737 095 516,14 RUB"


def test_currency_mismatch_and_inconsistent_snapshot_are_rejected():
    with pytest.raises(ValidationError, match="Валюта"):
        reconcile_snapshot(ACCOUNT, DAY, Money(0), Money(0, Currency("USD", 2)), [])
    with pytest.raises(VaultError, match="не соответствуют"):
        reconcile_snapshot(ACCOUNT, DAY, Money(1), Money(1), [])
