from dataclasses import replace
from datetime import date, time
from decimal import Decimal

import pytest

from personal_finance.domain.errors import ValidationError
from personal_finance.domain.ocr import OcrBox, OcrWord
from personal_finance.domain.ocr_cards import CashDirection, OcrIssue
from personal_finance.infrastructure.ocr.vtb import VtbCardAdapter
from personal_finance.infrastructure.ocr.vtb_history import VtbHistoryAdapter
from tests.ocr_fixtures import VTB_CARD as CARD
from tests.ocr_fixtures import document, image
from tests.ocr_fixtures import vtb_history_document as history


def card(lines=CARD, **kwargs):
    return VtbCardAdapter().parse(document(lines, **kwargs))


def test_vtb_card_uses_labelled_date_and_preserves_evidence():
    value = card()
    assert not value.blocked_reason and value.adapter == "vtb-card-v1"
    assert value.amount.value == 123456 and value.currency.value == "RUB"
    assert value.direction.value == CashDirection.OUT
    assert value.occurred_on.value == date(2026, 10, 27) and value.local_time.value == time(14, 35)
    assert value.occurred_on.word_indices == (2, 3)


@pytest.mark.parametrize("date_line", ["27 октября", "27 октября 14:35", "31.02.2026"])
def test_vtb_date_without_year_or_invalid_calendar_is_never_guessed(date_line):
    assert card((*CARD[:3], date_line)).occurred_on.value is None


def test_vtb_ignores_unlabelled_dates_and_distant_values():
    assert card((CARD[0], CARD[1], CARD[3])).occurred_on.value is None
    doc = document(CARD)
    words = (*doc.words[:3], replace(doc.words[3], box=OcrBox(50, 1200, 900, 50)))
    assert VtbCardAdapter().parse(replace(doc, words=words)).occurred_on.value is None


def test_vtb_inline_labels_and_unsigned_labelled_amount():
    value = card(("Детали операции", "Сумма: 12,34", "Дата и время: 27.10.2026 14:35"))
    assert value.amount.value == 1234 and value.direction.value is None
    assert value.currency.value is None and value.occurred_on.value == date(2026, 10, 27)


def test_vtb_separate_amount_label_does_not_duplicate_its_value():
    value = card(("Детали операции", "Сумма", "-12,34 RUB", "Дата и время", "27.10.2026"))
    assert not value.blocked_reason and value.amount.value == 1234
    assert value.amount.word_indices == (1, 2)


def test_one_summary_and_one_labelled_amount_keep_both_sources():
    value = card((*CARD, "Сумма", "1 234,56 RUB"))
    assert not value.blocked_reason and value.amount.value == 123456
    assert value.direction.value == CashDirection.OUT
    assert set(value.amount.word_indices) == {1, 4, 5}
    assert value.direction.word_indices == (1,)


@pytest.mark.parametrize("second", ["1 234,57 RUB", "+1 234,56 RUB", "1 234,56 Р", "0,00 RUB"])
def test_summary_conflict_is_not_silently_resolved(second):
    assert card((*CARD, "Сумма", second)).blocked_reason


def test_repeated_summary_does_not_hide_status_or_fee():
    for status in ("Статус: Отменён", "В обработке", "Комиссия"):
        assert card((*CARD, "Сумма", "1 234,56 RUB", status)).blocked_reason


@pytest.mark.parametrize("extra", ["История операций", "Детали операции", "Комиссия: 5 RUB"])
def test_vtb_unknown_multiple_history_or_fee_cards_are_blocked(extra):
    assert card((*CARD, extra)).blocked_reason
    assert card(("Неизвестный макет", *CARD[1:])).blocked_reason


def test_vtb_confusable_currency_and_low_confidence_remain_visible():
    value = card((CARD[0], "-1 234,56 Р", *CARD[2:]), confidence=Decimal(70))
    assert value.currency.value is None and value.amount.value == 123456
    assert OcrIssue.LOW_CONFIDENCE in value.amount.issues


def test_vtb_history_keeps_rows_and_excludes_money_near_date_header():
    doc = history()
    result = VtbHistoryAdapter().parse(doc)
    assert not result.blocked_reason and len(result.cards) == 2
    assert result.unassigned_fragments == 1
    first, second = result.cards
    assert first.adapter == "vtb-history-v1" and first.document is doc
    assert first.amount.value == 123456 and second.amount.value == 5000
    assert first.occurred_on.value == date(2026, 10, 27)
    assert first.direction.value == CashDirection.OUT and second.direction.value == CashDirection.IN
    assert second.currency.value is None and first.description.value == "Вымышленный магазин"
    assert set(first.amount.word_indices).isdisjoint(first.occurred_on.word_indices)


@pytest.mark.parametrize("header", ["27 октября", "Сегодня", "Вчера", "ср, 27 октября"])
def test_vtb_history_missing_year_relative_or_weekday_never_use_pc_clock(header):
    result = VtbHistoryAdapter().parse(history(header=header))
    assert len(result.cards) == 2 and all(c.occurred_on.value is None for c in result.cards)


@pytest.mark.parametrize("text", ["-1 23,45 RUB", "-О,50 RUB", "-12.345,67 RUB", "-10 RUB +20 RUB"])
def test_vtb_history_does_not_take_a_smaller_numeric_tail(text):
    result = VtbHistoryAdapter().parse(history(values=(text,)))
    assert not result.cards


def test_vtb_history_unknown_heading_and_amount_on_left_are_not_accepted():
    assert VtbHistoryAdapter().parse(history(heading=False)).blocked_reason
    result = VtbHistoryAdapter().parse(history(amount_left=400))
    assert not result.cards and result.blocked_reason


def test_vtb_history_identical_rows_are_not_deduplicated_and_edge_is_blocked():
    result = VtbHistoryAdapter().parse(history(values=("-50 RUB", "-50 RUB")))
    assert len(result.cards) == 2
    result = VtbHistoryAdapter().parse(history(values=("-50 RUB",), bottom=True))
    assert result.cards[0].blocked_reason


@pytest.mark.parametrize("count", [100, 101])
def test_vtb_history_limit_does_not_truncate(count):
    result = VtbHistoryAdapter().parse(history(values=("-1 RUB",) * count))
    if count == 100:
        assert len(result.cards) == count and not result.blocked_reason
    else:
        assert not result.cards and result.blocked_reason


def test_vtb_history_pending_or_fee_blocks_review():
    doc = history()
    for text in ("В обработке", "Комиссия: 5 RUB"):
        extra = OcrWord(text, OcrBox(20, 1350, 400, 20), Decimal(95), (1, 1, 999), 1)
        assert VtbHistoryAdapter().parse(replace(doc, words=(*doc.words, extra))).blocked_reason


def test_bank_profile_is_validated_at_preview_boundary():
    from personal_finance.application.ocr_cards import OcrCardPreview
    from personal_finance.domain.ocr import LoadedOcrImage

    with pytest.raises(ValidationError):
        OcrCardPreview(LoadedOcrImage("3" * 64, image()), card(), "Неизвестный")


@pytest.mark.parametrize(
    "extra",
    [
        ("1 234,56 RUB",),
        ("Сумма", "1 234,56 RUB", "+50 RUB"),
        ("Сумма", "1 234,56 RUB", "Сумма: 1 234,56 RUB"),
    ],
)
def test_summary_merge_requires_exactly_one_label_and_two_displays(extra):
    assert card((*CARD, *extra)).blocked_reason


def test_unknown_relative_header_does_not_inherit_previous_full_year():
    doc = history(values=("-1 RUB",), summary=False)
    tail = (
        OcrWord("Вчера", OcrBox(20, 300, 200, 20), Decimal(95), (1, 1, 90), 1),
        OcrWord("Магазин", OcrBox(20, 350, 200, 20), Decimal(95), (1, 1, 91), 1),
        OcrWord("-2 RUB", OcrBox(700, 350, 300, 20), Decimal(95), (1, 1, 91), 2),
    )
    result = VtbHistoryAdapter().parse(replace(doc, words=(*doc.words, *tail)))
    assert len(result.cards) == 2
    assert result.cards[0].occurred_on.value == date(2026, 10, 27)
    assert result.cards[1].occurred_on.value is None


def test_invalid_amount_label_does_not_consume_a_status():
    value = card((*CARD, "Сумма", "В обработке"))
    assert value.blocked_reason


@pytest.mark.parametrize(
    "unknown", ["28 окт. 2026", "28/10/2026", "28 октября 2026, неизвестный формат"]
)
def test_unrecognized_next_date_header_clears_previous_context(unknown):
    doc = history(values=("-1 RUB",), summary=False)
    extra = (
        OcrWord(unknown, OcrBox(20, 300, 400, 20), Decimal(95), (1, 1, 90), 1),
        OcrWord("Магазин", OcrBox(20, 350, 200, 20), Decimal(95), (1, 1, 91), 1),
        OcrWord("-2 RUB", OcrBox(700, 350, 300, 20), Decimal(95), (1, 1, 91), 2),
    )
    result = VtbHistoryAdapter().parse(replace(doc, words=(*doc.words, *extra)))
    assert len(result.cards) == 1 and result.cards[0].amount.value == 100


def test_fee_label_without_amount_also_blocks_history():
    doc = history()
    extra = OcrWord("Комиссия", OcrBox(20, 1350, 400, 20), Decimal(95), (1, 1, 999), 1)
    assert VtbHistoryAdapter().parse(replace(doc, words=(*doc.words, extra))).blocked_reason


@pytest.mark.parametrize("summary", ["-2 ООО Р", "−2 000 P", "2 000Р", "2026 Р"])
def test_unreadable_header_total_keeps_partial_date_without_inventing_year_or_operation(summary):
    doc = history(header="28 октября " + summary, values=("-17 RUB",), summary=False)
    result = VtbHistoryAdapter().parse(doc)
    assert not result.blocked_reason and len(result.cards) == 1
    assert result.cards[0].amount.value == 1700
    assert result.cards[0].occurred_on.value is None
    assert result.cards[0].occurred_on.word_indices
    assert result.unassigned_fragments == 1


def test_equal_transfers_under_two_partial_headers_are_separate_proposals():
    doc = history(header="28 октября -2 ООО Р", values=("-17 RUB",), summary=False)
    extra = (
        OcrWord("26 октября -2 ООО Р", OcrBox(20, 300, 400, 20), Decimal(95), (1, 1, 90), 1),
        OcrWord("Вымышленный перевод", OcrBox(20, 350, 300, 20), Decimal(95), (1, 1, 91), 1),
        OcrWord("-17 RUB", OcrBox(700, 350, 300, 20), Decimal(95), (1, 1, 91), 2),
    )
    result = VtbHistoryAdapter().parse(replace(doc, words=(*doc.words, *extra)))
    assert len(result.cards) == 2 and all(c.amount.value == 1700 for c in result.cards)
    assert all(c.occurred_on.value is None for c in result.cards)
    assert set(result.cards[0].amount.word_indices).isdisjoint(result.cards[1].amount.word_indices)
    assert result.cards[0].occurred_on.word_indices != result.cards[1].occurred_on.word_indices


def test_bad_amount_letters_are_never_converted_by_header_recovery():
    result = VtbHistoryAdapter().parse(
        history(header="28 октября -2 ООО Р", values=("-О,50 RUB",), summary=False)
    )
    assert not result.cards
