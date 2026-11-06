"""Границы исходной суммы: поле видно, неполный источник не допускает запись."""

from dataclasses import replace
from datetime import date

import pytest

from personal_finance.domain.ocr import OcrBox
from personal_finance.domain.ocr_cards import CashDirection
from personal_finance.infrastructure.ocr.generic import GenericCardAdapter, GenericHistoryAdapter
from personal_finance.infrastructure.ocr.t_bank import TBankCardAdapter
from personal_finance.infrastructure.ocr.vtb import VtbCardAdapter
from tests.ocr_fixtures import document

PROFILES = (
    (GenericCardAdapter, ("Детали операции", "-50 RUB", "27.10.2026")),
    (TBankCardAdapter, ("Т-Банк", "Перевод", "-50 RUB", "27.10.2026")),
    (VtbCardAdapter, ("Детали операции", "-50 RUB", "Дата и время", "27.10.2026 14:35")),
)


def source_at(lines, edge, gap):
    source = document(lines)
    left, top, width, height = 650, 500, 180, 32
    if edge == "left":
        left = gap
    elif edge == "right":
        left = source.width - width - gap
    elif edge == "top":
        top = gap
    elif edge == "bottom":
        top = source.height - height - gap
    words = tuple(
        replace(word, box=OcrBox(left, top, width, height)) if word.text == "-50 RUB" else word
        for word in source.words
    )
    return replace(source, words=words)


@pytest.mark.parametrize("adapter,lines", PROFILES)
@pytest.mark.parametrize("edge", ("left", "right", "top", "bottom"))
@pytest.mark.parametrize("gap", (0, 8, 9))
def test_card_checks_every_amount_edge_without_changing_recognized_fields(
    adapter, lines, edge, gap
):
    source = source_at(lines, edge, gap)
    words = source.words
    result = adapter().parse(source)
    assert result.document is source and source.words == words
    assert result.amount.value == 5000 and result.currency.value == "RUB"
    assert result.direction.value == CashDirection.OUT
    assert result.occurred_on.value == date(2026, 10, 27)
    if gap <= 8:
        assert result.blocked_reason and "снимок" in result.blocked_reason.casefold()
    else:
        assert not result.blocked_reason


@pytest.mark.parametrize("edge", ("left", "right", "top", "bottom"))
def test_generic_history_keeps_clipped_row_for_review(edge):
    source = source_at(("История операций", "27.10.2026", "Учебный магазин", "-50 RUB"), edge, 0)
    # Дата и описание находятся в той же строке, даже если сама строка у края.
    money = next(word for word in source.words if word.text == "-50 RUB")
    words = tuple(
        replace(
            word,
            box=OcrBox(200 if word.text == "27.10.2026" else 430, money.box.top, 160, 32),
            line_key=money.line_key,
            word_number=2 if word.text == "27.10.2026" else 3,
        )
        if word.text in ("27.10.2026", "Учебный магазин")
        else word
        for word in source.words
    )
    source = replace(source, words=words)
    result = GenericHistoryAdapter().parse(source)
    assert len(result.cards) == 1
    card = result.cards[0]
    assert card.document is source and card.amount.value == 5000
    assert card.blocked_reason and "снимок" in card.blocked_reason.casefold()


@pytest.mark.parametrize("edge", ("left", "right", "top", "bottom"))
def test_repeated_vtb_amount_cannot_hide_clipped_summary(edge):
    source = source_at(
        ("Детали операции", "-50 RUB", "Дата и время", "27.10.2026 14:35", "Сумма: 50 RUB"), edge, 0
    )
    result = VtbCardAdapter().parse(source)
    assert result.amount.value == 5000 and len(result.amount.word_indices) == 2
    assert result.blocked_reason and "снимок" in result.blocked_reason.casefold()


def test_existing_reason_is_not_replaced_by_source_bounds():
    source = source_at(("Т-Банк", "Перевод", "-50 RUB", "27.10.2026", "В обработке"), "right", 0)
    result = TBankCardAdapter().parse(source)
    assert "Непроведённая" in result.blocked_reason


@pytest.mark.parametrize("split", (False, True))
def test_clipped_adjacent_unrecognized_currency_blocks_numeric_source(split):
    source = source_at(("Детали операции", "-50 RUB", "27.10.2026"), "inside", 40)
    money = next(i for i, w in enumerate(source.words) if w.text == "-50 RUB")
    numeric = replace(source.words[money], text="-50", box=OcrBox(800, 500, 140, 32))
    if split:
        tail = (
            replace(numeric, text="R", box=OcrBox(960, 500, 25, 32), line_key=(1, 2, 1)),
            replace(numeric, text="U", box=OcrBox(997, 500, 25, 32), line_key=(1, 2, 2)),
            replace(numeric, text="E", box=OcrBox(1034, 500, 46, 32), line_key=(1, 2, 3)),
        )
    else:
        tail = (replace(numeric, text="RUE", box=OcrBox(960, 500, 120, 32), line_key=(1, 2, 1)),)
    source = replace(
        source, words=(*source.words[:money], numeric, *source.words[money + 1 :], *tail)
    )
    result = GenericCardAdapter().parse(source)
    assert result.amount.value == 5000 and result.document is source
    assert result.blocked_reason and "снимок" in result.blocked_reason.casefold()


@pytest.mark.parametrize("box", (OcrBox(0, 500, 100, 32), OcrBox(800, 0, 140, 32)))
def test_detached_edge_word_does_not_block_complete_amount(box):
    source = source_at(("Детали операции", "-50 RUB", "27.10.2026"), "inside", 40)
    extra = replace(source.words[0], text="Примечание", box=box, line_key=(1, 2, 1))
    result = GenericCardAdapter().parse(replace(source, words=(*source.words, extra)))
    assert result.amount.value == 5000 and not result.blocked_reason
