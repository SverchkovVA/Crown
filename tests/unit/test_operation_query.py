from dataclasses import replace
from datetime import date
from uuid import uuid4

import pytest

from personal_finance.domain.errors import ValidationError
from personal_finance.domain.ledger import Operation, OperationDraft, OperationKind, OperationPart
from personal_finance.domain.models import Money
from personal_finance.domain.query import OperationQuery

A, B, C, T, U, BANK = (str(uuid4()) for _ in range(6))
DAY = date(2026, 1, 15)


def operation(**kwargs):
    return Operation(
        str(uuid4()),
        str(uuid4()),
        OperationDraft(
            OperationKind.EXPENSE,
            DAY,
            A,
            Money(100),
            C,
            note="ПРОВЕРОЧНАЯ покупка",
            tag_ids=(T, U),
            **kwargs,
        ),
    )


def test_filter_roundtrip_and_order_of_sets_is_canonical():
    query = OperationQuery(
        DAY,
        DAY,
        account_ids=(B, A),
        tag_ids=(U, T),
        kinds=(OperationKind.REFUND, OperationKind.EXPENSE),
        text="  Покупка  ",
        include_deleted=True,
    )
    assert OperationQuery.from_dict(query.as_dict()) == query
    assert query.tag_ids == tuple(sorted((T, U))) and query.text == "Покупка"
    assert operation().draft.tag_ids == tuple(sorted((T, U)))


def test_groups_are_and_values_within_group_are_or_but_all_tags_required():
    value = operation()
    assert OperationQuery(
        start=DAY,
        end=DAY,
        account_ids=(A, B),
        category_ids=(C,),
        tag_ids=(T, U),
        text="проверочная",
    ).matches(value, {A: BANK})
    assert not OperationQuery(tag_ids=(str(uuid4()),)).matches(value, {})
    assert not OperationQuery(account_ids=(B,)).matches(value, {})
    assert not OperationQuery(text="другое").matches(value, {})
    assert not OperationQuery(kinds=(OperationKind.INCOME,)).matches(value, {})


def test_account_and_bank_must_match_same_side_of_transfer():
    value = Operation(
        str(uuid4()),
        str(uuid4()),
        OperationDraft(
            OperationKind.TRANSFER,
            DAY,
            A,
            Money(100),
            destination_account_id=B,
            fee=Money(1),
            fee_category_id=C,
        ),
    )
    assert OperationQuery(institution_ids=(BANK,)).matches(value, {B: BANK})
    assert OperationQuery(account_ids=(B,), institution_ids=(BANK,), category_ids=(C,)).matches(
        value, {B: BANK}
    )
    assert not OperationQuery(account_ids=(A,), institution_ids=(BANK,)).matches(value, {B: BANK})


def test_parts_dates_and_deleted_flags():
    value = operation()
    split = replace(
        value,
        draft=replace(
            value.draft,
            category_id=None,
            parts=(OperationPart(C, Money(60)), OperationPart(B, Money(40))),
        ),
    )
    assert OperationQuery(category_ids=(B,)).matches(split, {})
    assert not OperationQuery(start=date(2026, 1, 16)).matches(value, {})
    assert not OperationQuery(end=date(2026, 1, 14)).matches(value, {})
    assert not OperationQuery().matches(replace(value, is_deleted=True), {})
    assert OperationQuery(include_deleted=True).matches(replace(value, is_deleted=True), {})


@pytest.mark.parametrize(
    "field,value",
    [
        ("start", "2026-01-15"),
        ("end", True),
        ("account_ids", [A]),
        ("tag_ids", (T, T)),
        ("tag_ids", (1,)),
        ("kinds", ("expense",)),
        ("kinds", (OperationKind.INCOME, OperationKind.INCOME)),
        ("include_deleted", 1),
        ("text", "x" * 501),
        ("text", "x\t"),
    ],
)
def test_invalid_query_fields_are_rejected(field, value):
    with pytest.raises(ValidationError):
        OperationQuery(**{field: value})


@pytest.mark.parametrize(
    "change",
    [
        {"version": True},
        {"version": 2},
        {"unknown": 1},
        {"start": "20260115"},
        {"tag_ids": T},
        {"kinds": ["unknown"]},
    ],
)
def test_invalid_saved_filter_payload_is_rejected(change):
    payload = OperationQuery().as_dict() | change
    with pytest.raises(ValidationError):
        OperationQuery.from_dict(payload)


def test_bad_range_and_repeated_operation_tags_are_rejected():
    with pytest.raises(ValidationError):
        OperationQuery(start=date(2026, 2, 1), end=DAY)
    with pytest.raises(ValidationError):
        replace(operation().draft, tag_ids=(T, T))
