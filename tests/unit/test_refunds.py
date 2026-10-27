from dataclasses import replace
from datetime import date, timedelta
from uuid import uuid4

import pytest
from hypothesis import given
from hypothesis import strategies as st

from personal_finance.domain.errors import ValidationError
from personal_finance.domain.ledger import (
    LedgerRole,
    Operation,
    OperationDraft,
    OperationKind,
    OperationPart,
)
from personal_finance.domain.models import MAX_MINOR, Money
from personal_finance.domain.refunds import calculate_refund_candidates, validate_refund_reference

DAY = date(2026, 10, 27)
ACCOUNT = str(uuid4())
CATEGORY = str(uuid4())
OTHER = str(uuid4())
SOURCE = str(uuid4())
REVISION = str(uuid4())


def expense(amount=1000):
    return Operation(
        SOURCE,
        REVISION,
        OperationDraft(OperationKind.EXPENSE, DAY, ACCOUNT, Money(amount), category_id=CATEGORY),
    )


def refund(amount=1000, source=None):
    source = source or expense()
    return Operation(
        str(uuid4()),
        str(uuid4()),
        OperationDraft(
            OperationKind.REFUND,
            DAY + timedelta(days=1),
            ACCOUNT,
            Money(amount),
            category_id=CATEGORY,
            refund_of=source.id,
            refund_of_revision_id=source.revision_id,
        ),
    )


@given(st.integers(min_value=1, max_value=MAX_MINOR))
def test_refund_posts_positive_asset_and_negative_expense_without_income(amount):
    value = refund(amount, expense(amount)).draft
    posts = value.postings()
    assert [p.role for p in posts] == [LedgerRole.ASSET, LedgerRole.EXPENSE]
    assert [p.amount.minor for p in posts] == [amount, -amount]
    assert sum(p.amount.minor for p in posts) == 0


@pytest.mark.parametrize(
    "changes",
    [
        {"refund_of": None},
        {"refund_of_revision_id": None},
        {"refund_of": "bad"},
        {"refund_of_revision_id": "bad"},
        {"amount": Money(0)},
        {"amount": Money(-1)},
    ],
)
def test_refund_requires_source_and_positive_amount(changes):
    with pytest.raises(ValidationError):
        replace(refund().draft, **changes)


def test_non_refund_cannot_keep_refund_links():
    with pytest.raises(ValidationError, match="только для возврата"):
        replace(refund().draft, kind=OperationKind.INCOME)


def test_reference_requires_exact_expense_revision():
    source = expense()
    validate_refund_reference(refund(source=source).draft, source)
    for invalid in [
        replace(source, id=str(uuid4())),
        replace(source, revision_id=str(uuid4())),
        replace(source, is_deleted=True),
        replace(source, draft=replace(source.draft, kind=OperationKind.INCOME)),
    ]:
        with pytest.raises(ValidationError):
            validate_refund_reference(refund().draft, invalid)


def test_return_date_and_category_limits_apply_to_reference():
    source = expense()
    for invalid in [
        replace(refund().draft, occurred_on=DAY - timedelta(days=1)),
        replace(refund().draft, amount=Money(1001)),
        replace(refund().draft, category_id=OTHER),
    ]:
        with pytest.raises(ValidationError):
            validate_refund_reference(invalid, source)


def test_multiple_partial_refunds_leave_exact_remaining_and_deleted_do_not_count():
    source = expense()
    first = refund(600)
    second = refund(300)
    deleted = replace(refund(1000), is_deleted=True)
    candidate = calculate_refund_candidates([source, first, second, deleted])[0]
    assert candidate.amount_remaining == Money(100)
    assert candidate.amount_refunded == Money(900)
    assert calculate_refund_candidates([source, first, second], excluding_refund_id=first.id)[
        0
    ].amount_remaining == Money(700)
    with pytest.raises(ValidationError, match="Общая сумма возвратов"):
        calculate_refund_candidates([source, first, second, refund(101)])


def test_refunds_for_split_expense_respect_each_category():
    source = replace(
        expense(),
        draft=replace(
            expense().draft,
            category_id=None,
            parts=(OperationPart(CATEGORY, Money(700)), OperationPart(OTHER, Money(300))),
        ),
    )
    first = replace(
        refund(500, source),
        draft=replace(
            refund(500, source).draft,
            category_id=None,
            parts=(OperationPart(CATEGORY, Money(400)), OperationPart(OTHER, Money(100))),
        ),
    )
    validate_refund_reference(first.draft, source)
    candidate = calculate_refund_candidates([source, first])[0]
    assert candidate.remaining_parts == (
        OperationPart(CATEGORY, Money(300)),
        OperationPart(OTHER, Money(200)),
    )
    with pytest.raises(ValidationError):
        calculate_refund_candidates([source, refund(701, source)])


def test_expense_with_active_refund_cannot_disappear_change_kind_or_shrink():
    source = expense()
    first = refund(800)
    for invalid in [
        replace(source, is_deleted=True),
        replace(source, draft=replace(source.draft, kind=OperationKind.INCOME)),
        replace(source, draft=replace(source.draft, amount=Money(799))),
        replace(source, draft=replace(source.draft, occurred_on=DAY + timedelta(days=2))),
    ]:
        with pytest.raises(ValidationError):
            calculate_refund_candidates([invalid, first])


def test_full_refund_returns_zero_remaining_and_no_extra_capacity():
    source = expense()
    candidate = calculate_refund_candidates([source, refund()])[0]
    assert candidate.remaining_parts == () and candidate.amount_remaining == Money(0)
    with pytest.raises(ValidationError):
        calculate_refund_candidates([source, refund(), refund(1)])


@given(
    st.integers(min_value=1, max_value=MAX_MINOR // 2),
    st.integers(min_value=1, max_value=MAX_MINOR // 2),
)
def test_split_refund_exactly_reverses_expense_categories(left, right):
    parts = (OperationPart(CATEGORY, Money(left)), OperationPart(OTHER, Money(right)))
    source = replace(
        expense(left + right),
        draft=replace(expense(left + right).draft, category_id=None, parts=parts),
    )
    returned = replace(
        refund(left + right, source),
        draft=replace(refund(left + right, source).draft, category_id=None, parts=parts),
    )
    validate_refund_reference(returned.draft, source)
    assert [post.amount.minor for post in returned.draft.postings()] == [
        left + right,
        -left,
        -right,
    ]
    assert all(post.role != LedgerRole.INCOME for post in returned.draft.postings())
    assert calculate_refund_candidates([source, returned])[0].amount_remaining == Money(0)


def test_split_revision_hash_matches_published_schema4_fixture():
    from personal_finance.infrastructure.persistence.ledger_repository import revision_hash

    value = OperationDraft(
        OperationKind.EXPENSE,
        date(2026, 10, 27),
        "11111111-1111-1111-1111-111111111111",
        Money(10000),
        parts=(
            OperationPart("22222222-2222-2222-2222-222222222222", Money(7000)),
            OperationPart("33333333-3333-3333-3333-333333333333", Money(3000)),
        ),
        note="Синтетический расход 0004",
    )
    assert (
        revision_hash(value, False)
        == "71636bef7d33ac5be2d1550990b27a0442e14cb9b6985f968fd502033fb6c304"
    )
