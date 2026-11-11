"""Шапка списка и повреждённые знаки дневного итога — вымышленные данные."""

from datetime import date

import pytest

from personal_finance.infrastructure.ocr.generic import GenericHistoryAdapter
from tests.ocr_fixtures import document


@pytest.mark.parametrize("clock", ["0510Р", "0s:10P", "05:10 P"])
def test_status_clock_before_top_history_heading_is_not_an_operation(clock):
    source = document((clock, "История операций", "27 октября 2026", "Учебный магазин −73,15 RUB"))
    words = source.words
    result = GenericHistoryAdapter().parse(source)
    assert not result.blocked_reason
    assert [c.amount.value for c in result.cards] == [7315]
    assert result.cards[0].occurred_on.value == date(2026, 10, 27)
    assert result.document is source and source.words is words


def test_earlier_dated_transactions_are_not_trimmed_by_later_heading():
    source = document(
        ("27 октября 2026", "Учебный магазин −73,15 RUB", "История", "Пример +40 RUB")
    )
    result = GenericHistoryAdapter().parse(source)
    assert not result.blocked_reason
    assert [c.amount.value for c in result.cards] == [7315, 4000]
    assert all(c.occurred_on.value == date(2026, 10, 27) for c in result.cards)


def test_lower_navigation_history_label_does_not_trim_earlier_operations():
    lines = (
        "Учебный магазин −73,15 RUB",
        "Пример +40 RUB",
        "Учебная подпись",
        "Учебная подпись",
        "Учебная подпись",
        "Учебная подпись",
        "Учебная подпись",
        "История",
    )
    source = document(lines)
    result = GenericHistoryAdapter().parse(source)
    assert not result.blocked_reason
    assert [c.amount.value for c in result.cards] == [7315, 4000]


@pytest.mark.parametrize("mark", ["~", "≈", "∼"])
def test_approximation_only_residue_does_not_turn_daily_total_into_operation(mark):
    source = document(
        (
            "История операций",
            f"27 октября 2026 {mark}725,50 RUB",
            "Учебный магазин −73,15 RUB",
        )
    )
    words = source.words
    result = GenericHistoryAdapter().parse(source)
    assert not result.blocked_reason
    assert [c.amount.value for c in result.cards] == [7315]
    assert result.cards[0].occurred_on.value == date(2026, 10, 27)
    assert result.unassigned_fragments == 1
    assert result.document is source and source.words is words
    assert mark in source.words[1].text


@pytest.mark.parametrize("description", ["~Учебный магазин~", "Покупка ~ книги"])
def test_description_containing_approximation_mark_remains_transaction(description):
    source = document(("История операций", f"27 октября 2026 {description} −73,15 RUB"))
    result = GenericHistoryAdapter().parse(source)
    assert not result.blocked_reason
    assert [c.amount.value for c in result.cards] == [7315]
    assert result.cards[0].description.value == description


def test_planned_section_before_heading_keeps_nonfinancial_status():
    source = document(("Запланировано", "История операций", "27 октября 2026", "Пример −73,15 RUB"))
    result = GenericHistoryAdapter().parse(source)
    assert len(result.cards) == 1 and result.cards[0].blocked_reason
    assert result.cards[0].amount.value == 7315


def test_unbound_fee_before_heading_still_blocks_history():
    source = document(
        ("Комиссия: 10 RUB", "История операций", "27 октября 2026", "Пример −73,15 RUB")
    )
    result = GenericHistoryAdapter().parse(source)
    assert result.blocked_reason
    assert [c.amount.value for c in result.cards] == [7315]


@pytest.mark.parametrize(
    "header",
    [
        "27 октября 2026. —725,50 RUB",
        "27.10.2026. —725,50 RUB",
        "2026-10-27. 725,50 RUB",
        "27 октября 2026 . —725,50 RUB",
    ],
)
def test_final_period_does_not_turn_the_daily_total_into_an_operation(header):
    source = document(("История операций", header, "Учебный магазин −73,15 RUB"))
    history = GenericHistoryAdapter().parse(source)
    assert not history.blocked_reason
    assert len(history.cards) == 1
    (entry,) = history.cards
    assert entry.amount.value == 7315
    assert entry.occurred_on.value == date(2026, 10, 27)
    assert entry.occurred_on.word_indices == (1,)
    assert history.unassigned_fragments == 1
    assert history.document is source and source.words[1].text == header


def test_dated_transaction_with_description_after_the_period_is_not_a_daily_total():
    source = document(("История операций", "27 октября 2026. Учебный магазин —73,15 RUB"))
    history = GenericHistoryAdapter().parse(source)
    (entry,) = history.cards
    assert not history.blocked_reason
    assert entry.occurred_on.value == date(2026, 10, 27)
    assert entry.description.value == "Учебный магазин"
    assert entry.amount.value == 7315


@pytest.mark.parametrize("caption_first", [False, True])
def test_dated_total_next_to_summary_caption_still_supplies_the_day(caption_first):
    header = "27.10.2026. −725,50 RUB"
    summary = ("Траты", header) if caption_first else (header, "Траты")
    source = document(("История операций", *summary, "Учебный магазин −73,15 RUB"))
    history = GenericHistoryAdapter().parse(source)
    (entry,) = history.cards
    assert not history.blocked_reason
    assert entry.amount.value == 7315
    assert entry.occurred_on.value == date(2026, 10, 27)
    assert history.unassigned_fragments == 1
