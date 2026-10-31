"""Точные вымышленные форматы сумм и предложения года OCR."""

from dataclasses import replace
from datetime import date
from decimal import Decimal

import pytest

from personal_finance.domain.errors import ValidationError
from personal_finance.domain.ocr import OcrBox, OcrWord
from personal_finance.domain.ocr_cards import CashDirection, OcrField, OcrIssue
from personal_finance.infrastructure.ocr.rub_fields import suggest_occurred_on
from personal_finance.infrastructure.ocr.t_bank_history import TBankHistoryAdapter
from personal_finance.infrastructure.ocr.vtb import VtbCardAdapter
from personal_finance.infrastructure.ocr.vtb_history import VtbHistoryAdapter
from tests.ocr_fixtures import LINES, card, document, history_document, vtb_history_document


@pytest.mark.parametrize(
    "text,minor",
    [
        ("-7,5 RUB", 750),
        ("+8.4 ₽", 840),
        ("−9 876,3 руб.", 987630),
        ("-9\u00a0876,3 RUB", 987630),
        ("-9\u202f876.3 RUB", 987630),
        ("-0,1 RUB", 10),
        ("-7,0 RUB", 700),
    ],
)
def test_one_decimal_digit_is_exact_minor_units_in_both_cards(text, minor):
    tbank = card((*LINES[:2], text, *LINES[3:]))
    vtb = VtbCardAdapter().parse(document(("Детали операции", text, "Дата и время: 24.10.2026")))
    for value in (tbank, vtb):
        assert not value.blocked_reason and value.amount.value == minor
        assert type(value.amount.value) is int and value.currency.value == "RUB"
        assert value.amount.word_indices


def test_six_history_rows_with_one_decimal_digit_are_all_separate_proposals():
    values = ("-71,23 RUB", "+82,34 Р", "-93,4 RUB", "+104.5 P", "-115,67 RUB", "-126,78 RUB")
    result = TBankHistoryAdapter().parse(history_document(header="24 октября", values=values))
    assert not result.blocked_reason and len(result.cards) == 6
    assert [value.amount.value for value in result.cards] == [7123, 8234, 9340, 10450, 11567, 12678]
    assert all(value.occurred_on.value is None for value in result.cards)
    assert len({value.amount.word_indices for value in result.cards}) == 6


@pytest.mark.parametrize("text,minor", [("-73,4", 7340), ("+84.5", 8450), ("−95", 9500)])
def test_signed_history_amount_without_currency_retains_unknown_currency(text, minor):
    for adapter, doc in (
        (TBankHistoryAdapter(), history_document(values=(text,))),
        (VtbHistoryAdapter(), vtb_history_document(values=(text,), summary=False)),
    ):
        value = adapter.parse(doc)
        assert len(value.cards) == 1 and not value.blocked_reason
        assert value.cards[0].amount.value == minor
        assert value.cards[0].currency.value is None
        assert OcrIssue.UNRECOGNIZED in value.cards[0].currency.issues


@pytest.mark.parametrize(
    "text",
    ["-1 23,4 RUB", "-12.345,6 RUB", "-12,345 RUB", "-12,345", "-1 23,4", "-О,5 RUB", "73,4"],
)
def test_history_does_not_turn_invalid_groups_or_unknown_currency_unsigned_text_into_money(text):
    for adapter, doc in (
        (TBankHistoryAdapter(), history_document(values=(text,))),
        (VtbHistoryAdapter(), vtb_history_document(values=(text,), summary=False)),
    ):
        assert adapter.parse(doc).cards == ()


@pytest.mark.parametrize("year", [2024, 2027])
def test_partial_date_suggestion_uses_explicit_year_and_keeps_original(year):
    source = card((*LINES[:3], "24 октября", *LINES[4:]))
    proposal = suggest_occurred_on(source, reference_year=year)
    assert proposal.value == date(year, 10, 24)
    assert proposal.word_indices == source.occurred_on.word_indices
    assert source.occurred_on.value is None and OcrIssue.MISSING in source.occurred_on.issues
    assert OcrIssue.MISSING not in proposal.issues


@pytest.mark.parametrize("text", ["24 октября", "24 октября 09:17", "ср, 24 октября"])
def test_vtb_partial_date_with_label_or_weekday_keeps_origin(text):
    if text.startswith("ср"):
        source = (
            VtbHistoryAdapter().parse(vtb_history_document(header=text, summary=False)).cards[0]
        )
    else:
        source = VtbCardAdapter().parse(
            document(("Детали операции", "-73,4 RUB", "Дата и время", text))
        )
    proposal = suggest_occurred_on(source, reference_year=2026)
    assert proposal.value == date(2026, 10, 24)
    assert (
        proposal.word_indices == source.occurred_on.word_indices
        and source.occurred_on.value is None
    )


@pytest.mark.parametrize("tail", ["-7 ООО Р", "2027 Р", "-73,4 RUB"])
def test_vtb_header_total_never_becomes_a_year_in_date_proposal(tail):
    source = (
        VtbHistoryAdapter()
        .parse(vtb_history_document(header="24 октября " + tail, summary=False))
        .cards[0]
    )
    assert source.occurred_on.value is None
    assert suggest_occurred_on(source, reference_year=2026).value == date(2026, 10, 24)


@pytest.mark.parametrize("text", ["Сегодня", "Вчера", "24 окт.", "24 октября неизвестно"])
def test_unknown_or_relative_date_is_not_guessed(text):
    source = card((*LINES[:3], text, *LINES[4:]))
    assert suggest_occurred_on(source, reference_year=2026).value is None


def test_full_date_ambiguity_and_low_confidence_are_preserved():
    source = card(confidence=Decimal("72.5"))
    assert suggest_occurred_on(source, reference_year=2027) is source.occurred_on
    partial = card((*LINES[:3], "24 октября", *LINES[4:]), confidence=Decimal("72.5"))
    assert OcrIssue.LOW_CONFIDENCE in suggest_occurred_on(partial, reference_year=2026).issues
    ambiguous = replace(partial, occurred_on=OcrField(None, (3,), (OcrIssue.AMBIGUOUS,)))
    assert suggest_occurred_on(ambiguous, reference_year=2026) is ambiguous.occurred_on


def test_leap_day_suggestion_checks_the_explicit_year_calendar():
    source = card((*LINES[:3], "29 февраля", *LINES[4:]))
    assert suggest_occurred_on(source, reference_year=2024).value == date(2024, 2, 29)
    invalid = suggest_occurred_on(source, reference_year=2026)
    assert invalid.value is None and OcrIssue.INVALID in invalid.issues
    assert invalid.word_indices == source.occurred_on.word_indices


@pytest.mark.parametrize("year", [True, 2026.0, "2026", None, 0, 10000])
def test_date_suggestion_requires_explicit_valid_integer_year(year):
    with pytest.raises(ValidationError):
        suggest_occurred_on(card(), reference_year=year)


def test_vtb_repeated_summary_supports_equivalent_one_and_two_decimal_displays():
    value = VtbCardAdapter().parse(
        document(
            (
                "Детали операции",
                "-9 876,3 RUB",
                "Дата и время",
                "24.10.2026",
                "Сумма",
                "9 876,30 RUB",
            )
        )
    )
    assert not value.blocked_reason and value.amount.value == 987630
    assert value.direction.value == CashDirection.OUT
    assert set(value.amount.word_indices) == {1, 4, 5}


def inline_vtb_document(amount="-73,4", *, left=750, prefix=("Магазин", "пример")):
    base = document(("Детали операции", "Неиспользуемая строка", "Дата и время", "24.10.2026"))
    tokens = (*prefix, amount)
    inline = tuple(
        OcrWord(
            text,
            OcrBox(left if index == len(tokens) - 1 else 50 + index * 220, 160, 180, 30),
            Decimal(95),
            (1, 1, 2),
            index + 1,
        )
        for index, text in enumerate(tokens)
    )
    return replace(base, words=(base.words[0], *inline, *base.words[2:]))


@pytest.mark.parametrize("text,minor", [("-73,4", 7340), ("+84.5", 8450), ("-95,60 RUB", 9560)])
def test_vtb_inline_right_money_tail_keeps_exact_selected_words(text, minor):
    doc = inline_vtb_document(text, left=550)
    value = VtbCardAdapter().parse(doc)
    assert not value.blocked_reason and value.amount.value == minor
    assert value.amount.word_indices == (3,)
    assert value.occurred_on.value == date(2026, 10, 24)
    if not text.endswith("RUB"):
        assert value.currency.value is None
    assert doc.words[3].text == text


@pytest.mark.parametrize(
    "text", ["-1 23,4 RUB", "-12,345 RUB", "-О,5 RUB", "-73", "-10 RUB +20 RUB"]
)
def test_vtb_inline_tail_never_cuts_bad_or_multiple_values(text):
    assert VtbCardAdapter().parse(inline_vtb_document(text)).amount.value is None


def test_vtb_inline_left_money_is_not_inferred_as_right_layout():
    assert VtbCardAdapter().parse(inline_vtb_document(left=400)).amount.value is None


@pytest.mark.parametrize("prefix", [("В", "обработке"), ("Комиссия", "банка")])
def test_vtb_inline_split_preserves_pending_status_and_separate_fee(prefix):
    assert VtbCardAdapter().parse(inline_vtb_document(prefix=prefix)).blocked_reason


@pytest.mark.parametrize("first,second", [("Р", "P"), ("P", "Р")])
def test_vtb_equivalent_unknown_currency_glyphs_do_not_block_equal_summary(first, second):
    doc = document(
        (
            "Детали операции",
            "—9 876,3 " + first,
            "Дата и время",
            "24.10.2026",
            "Сумма",
            "9 876,30 " + second,
        )
    )
    value = VtbCardAdapter().parse(doc)
    assert not value.blocked_reason and value.amount.value == 987630
    assert value.currency.value is None and value.direction.value is None
    assert OcrIssue.UNRECOGNIZED in value.currency.issues
    assert set(value.amount.word_indices) == {1, 4, 5}
    assert value.document is doc


@pytest.mark.parametrize("label", ["Дата и время", "Дата проведения", "Дата операции"])
def test_vtb_inline_date_label_does_not_require_a_colon(label):
    value = VtbCardAdapter().parse(
        document(("Детали операции", "-123,4 RUB", label + " 24.10.2026 12:35"))
    )
    assert value.occurred_on.value == date(2026, 10, 24)


@pytest.mark.parametrize("label", ["Счёт", "Карта", "Номер", "Телефон", "ID"])
def test_vtb_signed_reference_number_is_not_an_inline_amount(label):
    assert (
        VtbCardAdapter().parse(inline_vtb_document("-1234", prefix=(label, "пример"))).amount.value
        is None
    )
