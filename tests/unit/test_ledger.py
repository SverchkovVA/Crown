from dataclasses import replace
from datetime import date, datetime
from uuid import uuid4

import pytest
from hypothesis import given
from hypothesis import strategies as st

from personal_finance.domain.errors import ValidationError
from personal_finance.domain.ledger import LedgerRole, OperationDraft, OperationKind
from personal_finance.domain.models import MAX_MINOR, Currency, Money

ACCOUNT = str(uuid4())
DESTINATION = str(uuid4())
CATEGORY = str(uuid4())
DAY = date(2026, 10, 26)


@pytest.mark.parametrize("kind", list(OperationKind))
def test_operation_posts_balanced_exact_amounts(kind):
    draft = OperationDraft(
        kind,
        DAY,
        ACCOUNT,
        Money(12345),
        category_id=CATEGORY if kind in (OperationKind.INCOME, OperationKind.EXPENSE) else None,
        destination_account_id=DESTINATION if kind == OperationKind.TRANSFER else None,
    )
    postings = draft.postings()
    assert sum(posting.amount.minor for posting in postings) == 0
    assert all(type(posting.amount.minor) is int for posting in postings)
    assert len(postings) == 2
    if kind == OperationKind.EXPENSE:
        assert postings[0].amount.minor == -12345
        assert postings[1].role == LedgerRole.EXPENSE
    elif kind == OperationKind.INCOME:
        assert postings[0].amount.minor == 12345
        assert postings[1].role == LedgerRole.INCOME


@given(st.integers(min_value=0, max_value=MAX_MINOR))
def test_opening_balance_accepts_zero_negative_and_extremes(minor):
    for value in (minor, -minor):
        postings = OperationDraft(OperationKind.OPENING, DAY, ACCOUNT, Money(value)).postings()
        assert postings[0].amount.minor == value
        assert postings[1].amount.minor == -value


def test_transfer_fee_is_expense_and_does_not_duplicate_income():
    postings = OperationDraft(
        OperationKind.TRANSFER,
        DAY,
        ACCOUNT,
        Money(10000),
        destination_account_id=DESTINATION,
        fee=Money(250),
        fee_category_id=CATEGORY,
    ).postings()
    assert [posting.amount.minor for posting in postings] == [-10250, 10000, 250]
    assert [posting.role for posting in postings] == [
        LedgerRole.ASSET,
        LedgerRole.ASSET,
        LedgerRole.EXPENSE,
    ]


@pytest.mark.parametrize(
    "changes",
    [
        {"amount": Money(0)},
        {"amount": Money(-1)},
        {"category_id": None},
        {"account_id": "не идентификатор"},
        {"occurred_on": datetime(2026, 10, 26)},
        {"note": "x" * 501},
        {"note": "\x00"},
        {"fee": Money(1)},
        {"destination_account_id": DESTINATION},
    ],
)
def test_invalid_income_is_rejected(changes):
    draft = OperationDraft(OperationKind.INCOME, DAY, ACCOUNT, Money(100), category_id=CATEGORY)
    with pytest.raises(ValidationError):
        replace(draft, **changes)


@pytest.mark.parametrize(
    "changes",
    [
        {"destination_account_id": ACCOUNT},
        {"destination_account_id": None},
        {"amount": Money(MAX_MINOR), "fee": Money(1), "fee_category_id": CATEGORY},
        {"fee": Money(-1)},
        {"fee": Money(1)},
        {"fee_category_id": CATEGORY},
        {"fee": Money(1, Currency("USD", 2))},
        {"category_id": CATEGORY},
    ],
)
def test_invalid_transfer_is_rejected(changes):
    draft = OperationDraft(
        OperationKind.TRANSFER,
        DAY,
        ACCOUNT,
        Money(100),
        destination_account_id=DESTINATION,
    )
    with pytest.raises(ValidationError):
        replace(draft, **changes)
