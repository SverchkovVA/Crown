from datetime import date
from uuid import uuid4

import pytest
from hypothesis import given
from hypothesis import strategies as st

from personal_finance.domain.errors import ValidationError
from personal_finance.domain.ledger import LedgerRole, OperationDraft, OperationKind, OperationPart
from personal_finance.domain.models import MAX_MINOR, Currency, Money

ACCOUNT = str(uuid4())
CATEGORIES = [str(uuid4()) for _ in range(101)]
DAY = date(2026, 10, 27)


@pytest.mark.parametrize("kind", [OperationKind.INCOME, OperationKind.EXPENSE])
@given(st.lists(st.integers(min_value=1, max_value=MAX_MINOR // 100), min_size=2, max_size=100))
def test_split_posts_exact_parts_and_one_asset_movement(kind, values):
    parts = tuple(
        OperationPart(category, Money(value))
        for category, value in zip(CATEGORIES, values, strict=False)
    )
    draft = OperationDraft(kind, DAY, ACCOUNT, Money(sum(values)), parts=parts)
    postings = draft.postings()
    sign = 1 if kind == OperationKind.INCOME else -1
    assert postings[0].role == LedgerRole.ASSET
    assert postings[0].amount.minor == sign * sum(values)
    assert [p.amount.minor for p in postings[1:]] == [-sign * v for v in values]
    assert [p.reference_id for p in postings[1:]] == CATEGORIES[: len(values)]
    assert sum(p.amount.minor for p in postings) == 0


@pytest.mark.parametrize("values", [(1, MAX_MINOR - 1), (MAX_MINOR - 1, 1)])
def test_split_extreme_total_remains_exact(values):
    parts = tuple(OperationPart(CATEGORIES[i], Money(v)) for i, v in enumerate(values))
    draft = OperationDraft(OperationKind.EXPENSE, DAY, ACCOUNT, Money(MAX_MINOR), parts=parts)
    assert sum(p.amount.minor for p in draft.postings()) == 0


@pytest.mark.parametrize(
    "parts,extra",
    [
        ((OperationPart(CATEGORIES[0], Money(1)),), {}),
        (tuple(OperationPart(c, Money(1)) for c in CATEGORIES), {}),
        ((OperationPart(CATEGORIES[0], Money(1)), OperationPart(CATEGORIES[0], Money(1))), {}),
        ((OperationPart(CATEGORIES[0], Money(1)), OperationPart(CATEGORIES[1], Money(2))), {}),
        (
            (OperationPart(CATEGORIES[0], Money(1)), OperationPart(CATEGORIES[1], Money(1))),
            {"category_id": CATEGORIES[0]},
        ),
        (
            (
                OperationPart(CATEGORIES[0], Money(1)),
                OperationPart(CATEGORIES[1], Money(1, Currency("USD", 2))),
            ),
            {},
        ),
        ([OperationPart(CATEGORIES[0], Money(1)), OperationPart(CATEGORIES[1], Money(1))], {}),
        (("не часть", "не часть"), {}),
    ],
)
def test_invalid_split_is_rejected(parts, extra):
    with pytest.raises(ValidationError):
        OperationDraft(OperationKind.EXPENSE, DAY, ACCOUNT, Money(2), parts=parts, **extra)


@pytest.mark.parametrize("kind", [OperationKind.OPENING, OperationKind.TRANSFER])
def test_opening_and_transfer_cannot_be_split(kind):
    with pytest.raises(ValidationError, match="Разделить можно"):
        OperationDraft(
            kind,
            DAY,
            ACCOUNT,
            Money(2),
            parts=(OperationPart(CATEGORIES[0], Money(1)), OperationPart(CATEGORIES[1], Money(1))),
        )


@pytest.mark.parametrize("minor", [0, -1])
def test_part_requires_positive_amount(minor):
    with pytest.raises(ValidationError):
        OperationPart(CATEGORIES[0], Money(minor))


def test_plain_revision_hash_matches_published_schema3_fixture():
    from personal_finance.infrastructure.persistence.ledger_repository import revision_hash

    value = OperationDraft(
        OperationKind.EXPENSE,
        date(2026, 10, 26),
        "11111111-1111-1111-1111-111111111111",
        Money(12345),
        "22222222-2222-2222-2222-222222222222",
        note="Синтетическая запись 0003",
    )
    # Эталон получен реализацией опубликованного коммита f6a1993, а не новым сериализатором.
    assert (
        revision_hash(value, False)
        == "1e4884c1a5aeec2e45c95d239188102ac0800ea0e8ca9b0ed49b739849b47b99"
    )
