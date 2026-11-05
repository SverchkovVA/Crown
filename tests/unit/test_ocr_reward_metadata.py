"""Только вымышленные макеты; бонусы не являются рублёвыми движениями."""

from dataclasses import replace
from datetime import date
from decimal import Decimal
from uuid import uuid4

import pytest

from personal_finance.domain.bonuses import BonusDraft, BonusKind, format_units, parse_units
from personal_finance.domain.errors import ValidationError
from personal_finance.domain.models import Category, CategoryKind
from personal_finance.domain.ocr import OcrBox, OcrDocument, OcrWord
from personal_finance.domain.ocr_cards import CashDirection
from personal_finance.infrastructure.ocr.generic import GenericCardAdapter, GenericHistoryAdapter
from personal_finance.presentation.ocr_metadata import bank_category_match
from tests.ocr_fixtures import ENGINE


def drawing(cells):
    words = []
    for line_number, (text, left, top, height) in enumerate(cells, 1):
        for word_number, token in enumerate(text.split(), 1):
            width = max(12, len(token) * height // 2)
            words.append(
                OcrWord(
                    token,
                    OcrBox(left, top, width, height),
                    Decimal(95),
                    (1, line_number, 1),
                    word_number,
                )
            )
            left += width + max(8, height // 3)
    return OcrDocument("a" * 64, 1400, 2600, ENGINE, tuple(words))


def reward_history(icon=False):
    cells = [
        ("История операций", 80, 100, 30),
        ("7 ноября", 80, 300, 42),
        ("Тестовый магазин", 150, 400, 40),
        ("-367,20 ₽", 1000, 400, 40),
        ("Продукты", 150, 458, 30),
        ("+3", 430, 458, 30),
        ("Тестовый счёт", 900, 458, 30),
        ("Вымышленный плательщик", 150, 600, 40),
        ("+450,00 ₽", 1000, 600, 40),
        ("Кешбэк", 150, 800, 40),
        ("+15,00 ₽", 1000, 800, 40),
    ]
    if icon:
        cells.append(("©", 70, 393, 105))
    return drawing(cells)


def purchase_card():
    return drawing(
        [
            ("11 ноября, 10:42", 200, 150, 42),
            ("Тестовый магазин", 200, 520, 38),
            ("Продукты", 200, 600, 30),
            ("-367,20 ₽", 450, 730, 70),
            ("Кешбэк за покупку", 90, 1150, 34),
            ("+3", 700, 1150, 34),
            ("Покупка по карте Справка", 90, 1400, 42),
            ("Тестовая карта", 90, 1520, 30),
            ("2 468,13 ₽", 90, 1600, 28),
            ("Рекламное предложение +900 ₽", 90, 1850, 28),
        ]
    )


@pytest.mark.parametrize("icon", [False, True])
def test_reward_subtitle_is_not_income_and_actual_cashback_remains_money(icon):
    history = GenericHistoryAdapter().parse(reward_history(icon))
    assert not history.blocked_reason and len(history.cards) == 3
    first, incoming, cashback = history.cards
    assert first.amount.value == 36720 and first.direction.value is CashDirection.OUT
    assert incoming.amount.value == 45000 and cashback.amount.value == 1500
    assert (
        incoming.direction.value is CashDirection.IN
        and cashback.direction.value is CashDirection.IN
    )
    assert len(first.metadata.bonuses) == 1 and first.metadata.bonuses[0].units_minor == 300
    assert first.metadata.bank_category == "Продукты"
    assert not incoming.metadata.bonuses and not cashback.metadata.bonuses


def test_card_purchase_footer_and_bonus_block_never_replace_primary_amount():
    card = GenericCardAdapter().parse(purchase_card())
    assert not card.blocked_reason
    assert card.amount.value == 36720 and card.direction.value is CashDirection.OUT
    assert card.metadata.bank_category == "Продукты"
    assert [b.units_minor for b in card.metadata.bonuses] == [300]
    assert card.adapter == "generic-card-v6"


def test_source_keys_stable_but_different_images_are_distinct():
    doc = reward_history()
    a = GenericHistoryAdapter().parse(doc).cards[0]
    b = GenericHistoryAdapter().parse(doc).cards[0]
    c = GenericHistoryAdapter().parse(replace(doc, image_sha256="b" * 64)).cards[0]
    assert a.metadata.bonuses == b.metadata.bonuses
    assert a.metadata.bonuses[0].source_key != c.metadata.bonuses[0].source_key


def test_category_hint_requires_unique_kind_and_exact_name():
    meta = GenericHistoryAdapter().parse(reward_history()).cards[0].metadata
    from personal_finance.domain.ledger import OperationKind

    def uid():
        return str(uuid4())

    one = Category(
        id=uid(), name="Продукты", kind=CategoryKind.EXPENSE, parent_id=None, revision_id=uid()
    )
    assert bank_category_match(meta, [one], OperationKind.EXPENSE) == one.id
    assert bank_category_match(meta, [one], OperationKind.INCOME) is None
    assert bank_category_match(meta, [one, replace(one, id=uid())], OperationKind.EXPENSE) is None


@pytest.mark.parametrize(
    "kind,value",
    [
        (BonusKind.ACCRUAL, 1),
        (BonusKind.SPENDING, -1),
        (BonusKind.EXPIRY, -1),
        (BonusKind.OPENING, 1),
        (BonusKind.ADJUSTMENT, -1),
    ],
)
def test_bonus_signed_minimal_units(kind, value):
    draft = BonusDraft(str(uuid4()), date(2026, 10, 31), kind, value)
    assert draft.units_minor == value


@pytest.mark.parametrize(
    "kind,value",
    [
        (BonusKind.ACCRUAL, -1),
        (BonusKind.SPENDING, 1),
        (BonusKind.EXPIRY, 1),
        (BonusKind.ACCRUAL, 0),
        (BonusKind.ACCRUAL, True),
        (BonusKind.ACCRUAL, 1.0),
    ],
)
def test_wrong_bonus_units_rejected(kind, value):
    with pytest.raises(ValidationError):
        BonusDraft(str(uuid4()), date(2026, 10, 31), kind, value)


@pytest.mark.parametrize(
    "text,minor", [("1", 100), ("1,25", 125), ("-1,25", -125), ("1 250,01", 125001)]
)
def test_exact_units(text, minor):
    assert parse_units(text) == minor
    assert parse_units(format_units(minor)) == minor


def test_percentages_and_explicit_points_never_become_ruble_income():
    doc = drawing(
        [
            ("История операций", 80, 100, 30),
            ("7 ноября", 80, 300, 42),
            ("Бонусы +3 балла", 150, 400, 30),
            ("Кешбэк +5%", 150, 500, 30),
            ("Тестовый плательщик", 150, 700, 40),
            ("+450,00 ₽", 1000, 700, 40),
        ]
    )
    value = GenericHistoryAdapter().parse(doc)
    assert len(value.cards) == 1 and value.cards[0].amount.value == 45000
