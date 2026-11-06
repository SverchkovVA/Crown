"""Соседняя подпись сводного виджета не должна создавать денежную операцию."""

import pytest

from personal_finance.infrastructure.ocr.generic import GenericCardAdapter, GenericHistoryAdapter
from tests.unit.test_generic_ocr import layout


def source(kind, rows):
    title = "Детали операции" if kind == "card" else "История операций"
    return layout([(title, 40, 20), ("27.10.2026", 40, 90), *rows])


def parse(kind, document):
    return (GenericCardAdapter() if kind == "card" else GenericHistoryAdapter()).parse(document)


def amounts(kind, result):
    return (
        []
        if kind == "card" and result.amount.value is None
        else (
            [result.amount.value]
            if kind == "card"
            else [card.amount.value for card in result.cards]
        )
    )


@pytest.mark.parametrize("kind", ["card", "history"])
@pytest.mark.parametrize("label", ["Траты", "Доходы", "Расходы", "Баланс", "Итого"])
@pytest.mark.parametrize("position", ["before", "after"])
def test_near_caption_in_same_column_is_not_a_financial_amount(kind, label, position):
    rows = (
        [(label, 40, 160), ("−5009,00 RUB", 40, 198)]
        if position == "before"
        else [
            ("−5009,00 RUB", 40, 160),
            (label, 40, 198),
        ]
    )
    document = source(kind, rows)
    original_words = document.words
    result = parse(kind, document)
    assert amounts(kind, result) == [] and result.blocked_reason
    assert document.words is original_words
    if kind == "history":
        assert result.unassigned_fragments == 1


@pytest.mark.parametrize("kind", ["card", "history"])
def test_distant_caption_does_not_suppress_an_amount(kind):
    result = parse(kind, source(kind, [("−73,15 RUB", 40, 160), ("Расходы", 40, 450)]))
    assert amounts(kind, result) == [7315] and not result.blocked_reason


@pytest.mark.parametrize("kind", ["card", "history"])
def test_caption_in_other_column_does_not_suppress_an_amount(kind):
    result = parse(kind, source(kind, [("Доходы", 40, 160), ("−73,15 RUB", 730, 198)]))
    assert amounts(kind, result) == [7315] and not result.blocked_reason


def test_real_name_before_amount_is_not_replaced_by_a_following_category_caption():
    document = source(
        "history",
        [
            ("Учебный магазин", 40, 160),
            ("−73,15 RUB", 40, 198),
            ("Расходы", 40, 236),
        ],
    )
    result = GenericHistoryAdapter().parse(document)
    assert amounts("history", result) == [7315]
    assert result.cards[0].description.value == "Учебный магазин"


def test_explicit_recipient_before_amount_keeps_a_real_card():
    document = source(
        "card",
        [
            ("Получатель: Учебный магазин", 40, 160),
            ("−73,15 RUB", 40, 198),
            ("Расходы", 40, 236),
        ],
    )
    result = GenericCardAdapter().parse(document)
    assert result.amount.value == 7315 and not result.blocked_reason
    assert result.description.value == "Учебный магазин"


def test_inline_description_and_following_caption_keep_real_history_row():
    result = GenericHistoryAdapter().parse(
        source(
            "history",
            [
                ("Учебный магазин −73,15 RUB", 40, 160),
                ("Расходы", 40, 198),
            ],
        )
    )
    assert amounts("history", result) == [7315]
    assert result.cards[0].description.value == "Учебный магазин"


@pytest.mark.parametrize("label", ["Списано", "Зачислено"])
def test_movement_field_is_not_a_summary_caption(label):
    result = GenericCardAdapter().parse(source("card", [(label, 40, 160), ("73,15 RUB", 40, 198)]))
    assert result.amount.value == 7315 and not result.blocked_reason


def test_multiword_description_before_amount_remains_a_counterparty():
    document = source(
        "history",
        [
            ("Расходы на материалы", 40, 160),
            ("−73,15 RUB", 40, 198),
        ],
    )
    result = GenericHistoryAdapter().parse(document)
    assert amounts("history", result) == [7315]
    assert result.cards[0].description.value == "Расходы на материалы"


def test_summary_screen_heading_is_not_a_recipient_protecting_summary_amount():
    document = source(
        "history",
        [
            ("Сводка за день", 40, 160),
            ("−5009,00 RUB", 40, 198),
            ("Траты", 40, 236),
        ],
    )
    result = GenericHistoryAdapter().parse(document)
    assert not result.cards and result.blocked_reason
