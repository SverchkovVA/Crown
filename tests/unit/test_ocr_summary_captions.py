"""Подпись сводного показателя с суммой не является отдельной операцией."""

from datetime import date

import pytest

from personal_finance.infrastructure.ocr.generic import GenericCardAdapter, GenericHistoryAdapter
from tests.ocr_fixtures import document


@pytest.mark.parametrize("label", ["Траты", "Доходы", "Расходы"])
@pytest.mark.parametrize("position", ["before", "after"])
def test_total_caption_and_value_are_not_history_rows(label, position):
    text = f"{label}: +5 600,00 RUB" if position == "before" else f"+5 600,00 RUB {label}"
    source = document(("Сводка за день", "27.10.2026", text))
    result = GenericHistoryAdapter().parse(source)
    assert not result.cards and result.blocked_reason
    assert result.unassigned_fragments == 1
    assert result.document is source


@pytest.mark.parametrize("label", ["Траты", "Доходы", "Расходы"])
@pytest.mark.parametrize("position", ["before", "after"])
def test_total_caption_cannot_be_the_main_card_amount(label, position):
    text = f"{label}: −5 009,00 RUB" if position == "before" else f"−5 009,00 RUB {label}"
    source = document(("Детали операции", "27.10.2026", text))
    result = GenericCardAdapter().parse(source)
    assert result.amount.value is None and result.blocked_reason
    assert result.occurred_on.value == date(2026, 10, 27)
    assert result.document is source


@pytest.mark.parametrize(
    "description", ["Траты ООО", "Доходы от аренды", "Расходы на материалы", "Доходный магазин"]
)
def test_description_with_more_words_is_preserved(description):
    source = document(("История", "27.10.2026", description + " +73,15 RUB"))
    result = GenericHistoryAdapter().parse(source)
    assert not result.blocked_reason and len(result.cards) == 1
    assert result.cards[0].amount.value == 7315
    assert result.cards[0].description.value == description


def test_summary_rows_do_not_hide_individual_operations_or_change_source():
    source = document(
        (
            "История операций",
            "27.10.2026",
            "Траты −5 009,00 RUB",
            "Доходы +5 600,00 RUB",
            "Учебный магазин −73,15 RUB",
            "Учебное поступление +149,07 RUB",
        )
    )
    words = source.words
    result = GenericHistoryAdapter().parse(source)
    assert not result.blocked_reason and len(result.cards) == 2
    assert [card.amount.value for card in result.cards] == [7315, 14907]
    assert all(card.adapter == "generic-history-v7" for card in result.cards)
    assert result.unassigned_fragments == 2
    assert source.words is words


def test_card_uses_actual_amount_and_keeps_summary_as_source_only():
    source = document(("Детали операции", "+73,15 RUB", "27.10.2026", "Доходы +5 600,00 RUB"))
    card = GenericCardAdapter().parse(source)
    assert not card.blocked_reason and card.amount.value == 7315
    assert card.adapter == "generic-card-v9" and card.document is source
    assert "Доходы" in " ".join(word.text for word in card.document.words)
