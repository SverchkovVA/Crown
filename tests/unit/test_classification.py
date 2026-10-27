from dataclasses import replace
from itertools import permutations
from uuid import uuid4

import pytest

from personal_finance.domain.classification import (
    MAX_CATEGORY_RULES,
    CategoryRule,
    ClassificationInput,
    ClassificationStatus,
    suggest_category,
)
from personal_finance.domain.errors import ValidationError
from personal_finance.domain.ledger import OperationKind, OperationPart
from personal_finance.domain.models import Category, CategoryKind, Money

ACCOUNT, OTHER, FOOD, OTHER_EXPENSE, SALARY = (str(uuid4()) for _ in range(5))
CATEGORIES = (
    Category(FOOD, "Продукты", CategoryKind.EXPENSE),
    Category(OTHER_EXPENSE, "Прочее", CategoryKind.EXPENSE),
    Category(SALARY, "Зарплата", CategoryKind.INCOME),
)


def rule(**changes):
    return replace(
        CategoryRule(str(uuid4()), "Магазин", OperationKind.EXPENSE, "магазин", FOOD), **changes
    )


def context(**changes):
    return replace(
        ClassificationInput(OperationKind.EXPENSE, ACCOUNT, "Покупка МАГАЗИН"), **changes
    )


def test_literal_unicode_casefold_suggestion_has_explainable_rule_identity():
    value = rule(contains="straße")
    result = suggest_category(context(text="Платёж STRASSE"), (value,), CATEGORIES)
    assert result.status == ClassificationStatus.SUGGESTED
    assert result.category_id == FOOD and result.rule_ids == (value.id,) and result.priority == 0
    assert suggest_category(context(), (rule(),), CATEGORIES).category_id == FOOD


def test_regex_characters_are_literal_and_do_not_expand_matches():
    value = rule(contains="магазин.*[1]")
    assert suggest_category(context(), (value,), CATEGORIES).status == ClassificationStatus.NO_MATCH
    assert suggest_category(context(text="МАГАЗИН.*[1]"), (value,), CATEGORIES).category_id == FOOD


def test_kind_account_and_text_must_all_match_and_disabled_rules_are_ignored():
    candidates = (
        rule(account_id=OTHER),
        rule(kind=OperationKind.INCOME, category_id=SALARY),
        rule(enabled=False),
    )
    assert (
        suggest_category(context(), candidates, CATEGORIES).status == ClassificationStatus.NO_MATCH
    )
    value = rule(account_id=ACCOUNT)
    assert suggest_category(context(), (value,), CATEGORIES).category_id == FOOD
    assert (
        suggest_category(context(text="Другая покупка"), (value,), CATEGORIES).status
        == ClassificationStatus.NO_MATCH
    )


def test_highest_priority_is_independent_of_rule_order():
    candidates = (rule(priority=1), rule(priority=10, category_id=OTHER_EXPENSE), rule(priority=0))
    for ordered in permutations(candidates):
        result = suggest_category(context(), ordered, CATEGORIES)
        assert result.category_id == OTHER_EXPENSE and result.rule_ids == (candidates[1].id,)
        assert result.priority == 10


def test_equal_priority_different_categories_return_conflict_without_arbitrary_choice():
    candidates = (rule(priority=4), rule(priority=4, category_id=OTHER_EXPENSE), rule(priority=3))
    for ordered in permutations(candidates):
        result = suggest_category(context(), ordered, CATEGORIES)
        assert result.status == ClassificationStatus.CONFLICT and result.category_id is None
        assert (
            result.rule_ids == tuple(sorted(r.id for r in candidates[:2])) and result.priority == 4
        )


def test_equal_priority_same_category_is_one_explained_suggestion():
    candidates = (rule(priority=4), rule(priority=4))
    result = suggest_category(context(), candidates, CATEGORIES)
    assert result.status == ClassificationStatus.SUGGESTED and result.category_id == FOOD
    assert result.rule_ids == tuple(sorted(r.id for r in candidates))


def test_manual_category_has_priority_even_when_rule_points_elsewhere_or_is_stale():
    value = context(manual_category_id=OTHER_EXPENSE)
    result = suggest_category(value, (rule(category_id=str(uuid4())),), CATEGORIES)
    assert result.status == ClassificationStatus.MANUAL and result.category_id == OTHER_EXPENSE
    assert not result.rule_ids and value.manual_category_id == OTHER_EXPENSE


def test_manual_parts_are_not_replaced_by_a_single_category():
    parts = (OperationPart(FOOD, Money(80)), OperationPart(OTHER_EXPENSE, Money(20)))
    value = context(manual_parts=parts)
    result = suggest_category(value, (rule(),), CATEGORIES)
    assert result.status == ClassificationStatus.MANUAL and result.category_id is None
    assert value.manual_parts is parts and not result.rule_ids


@pytest.mark.parametrize(
    "kind", [OperationKind.OPENING, OperationKind.TRANSFER, OperationKind.REFUND]
)
def test_transfer_opening_and_refund_never_acquire_rule_category(kind):
    result = suggest_category(context(kind=kind), (rule(),), CATEGORIES)
    assert result.status == ClassificationStatus.UNSUPPORTED and result.category_id is None


@pytest.mark.parametrize("category", [SALARY, str(uuid4())])
def test_missing_or_wrong_kind_category_of_matching_rule_is_not_silently_used(category):
    with pytest.raises(ValidationError, match="Категория совпавшего"):
        suggest_category(context(), (rule(category_id=category),), CATEGORIES)


def test_empty_rules_and_no_match_do_not_invent_default_category():
    result = suggest_category(context(), (), CATEGORIES)
    assert result.status == ClassificationStatus.NO_MATCH and result.category_id is None


def test_duplicate_rule_ids_and_oversized_collections_are_rejected():
    value = rule()
    for values in (
        (value, replace(value, category_id=OTHER_EXPENSE)),
        tuple(rule() for _ in range(MAX_CATEGORY_RULES + 1)),
    ):
        with pytest.raises(ValidationError, match="набор правил"):
            suggest_category(context(), values, CATEGORIES)


@pytest.mark.parametrize(
    "changes",
    [
        {"contains": ""},
        {"contains": "  "},
        {"contains": "a" * 201},
        {"contains": "a\nb"},
        {"priority": True},
        {"priority": 1.0},
        {"priority": -1},
        {"priority": 1000},
        {"kind": OperationKind.TRANSFER},
        {"kind": "expense"},
        {"enabled": 1},
        {"category_id": "bad-id"},
        {"account_id": "bad-id"},
        {"name": ""},
    ],
)
def test_invalid_rule_configuration_is_rejected(changes):
    with pytest.raises(ValidationError):
        rule(**changes)


@pytest.mark.parametrize(
    "changes",
    [
        {"text": "a" * 501},
        {"text": "a\x00b"},
        {"kind": "expense"},
        {"account_id": "bad-id"},
        {"manual_category_id": "bad-id"},
        {"manual_parts": []},
        {"manual_category_id": FOOD, "manual_parts": (OperationPart(OTHER_EXPENSE, Money(1)),)},
    ],
)
def test_invalid_classification_input_is_rejected(changes):
    with pytest.raises(ValidationError):
        context(**changes)


def test_income_uses_income_category_and_category_lists_cannot_repeat_ids():
    value = rule(kind=OperationKind.INCOME, category_id=SALARY)
    result = suggest_category(context(kind=OperationKind.INCOME), (value,), CATEGORIES)
    assert result.category_id == SALARY
    with pytest.raises(ValidationError, match="повторяется"):
        suggest_category(context(), (rule(),), CATEGORIES + (CATEGORIES[0],))
