"""Сумма не должна собираться из чисел разных колонок."""

from decimal import Decimal

import pytest

from personal_finance.domain.ocr import OcrBox, OcrDocument, OcrWord
from personal_finance.domain.ocr_cards import OcrIssue
from personal_finance.infrastructure.ocr.generic import GenericCardAdapter, GenericHistoryAdapter
from tests.ocr_fixtures import ENGINE


def source_for(kind, *, first="1", next_left=650, currency_left=820, single_word=False):
    pieces = [
        ("Детали операции" if kind == "card" else "История операций", 40, 30, 400),
        ("27 октября 2026", 40, 95, 300),
        ("Учебный магазин", 40, 200, 230),
    ]
    if single_word:
        pieces.append(("1 732,19", 600, 200, 180))
    else:
        pieces.extend(((first, 300, 200, 16), ("732,19", next_left, 200, 100)))
    pieces.append(("RUB", currency_left, 200, 60))
    words = tuple(
        OcrWord(text, OcrBox(left, top, width, 28), Decimal(96), (1, i + 1, 1), 1)
        for i, (text, left, top, width) in enumerate(pieces)
    )
    return OcrDocument("b" * 64, 1080, 1400, ENGINE, words)


def parse(kind, source):
    if kind == "card":
        return GenericCardAdapter().parse(source)
    result = GenericHistoryAdapter().parse(source)
    assert len(result.cards) == 1
    return result.cards[0]


@pytest.mark.parametrize("kind", ["card", "history"])
@pytest.mark.parametrize("first", ["1", "−1"])
def test_numeric_parts_from_distant_columns_remain_ambiguous(kind, first):
    source = source_for(kind, first=first)
    words = source.words
    result = parse(kind, source)
    assert result.amount.value is None
    assert OcrIssue.AMBIGUOUS in result.amount.issues
    assert result.blocked_reason and result.amount.word_indices
    assert result.document is source and source.words is words


@pytest.mark.parametrize("kind", ["card", "history"])
def test_adjacent_thousands_groups_are_kept_exactly(kind):
    source = source_for(kind, next_left=330, currency_left=460)
    result = parse(kind, source)
    assert result.amount.value == 173219 and not result.blocked_reason


@pytest.mark.parametrize("kind", ["card", "history"])
def test_separate_currency_column_does_not_break_adjacent_digits(kind):
    source = source_for(kind, next_left=330, currency_left=950)
    result = parse(kind, source)
    assert result.amount.value == 173219 and result.currency.value == "RUB"
    assert not result.blocked_reason


@pytest.mark.parametrize("kind", ["card", "history"])
def test_single_recognized_word_keeps_its_number(kind):
    source = source_for(kind, single_word=True)
    result = parse(kind, source)
    assert result.amount.value == 173219 and not result.blocked_reason
