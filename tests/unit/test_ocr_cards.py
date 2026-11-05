from dataclasses import replace
from datetime import date, time
from decimal import Decimal

import pytest

from personal_finance.application.ocr import Ocr
from personal_finance.application.ocr_cards import CardRecognition
from personal_finance.domain.errors import ValidationError
from personal_finance.domain.ocr import LoadedOcrImage
from personal_finance.domain.ocr_cards import (
    CardCorrections,
    CashDirection,
    OcrIssue,
    review_card,
)
from personal_finance.infrastructure.ocr.t_bank import TBankCardAdapter
from tests.ocr_fixtures import LINES, card, document, image

CONFIRMED = frozenset({"amount", "currency", "direction", "occurred_on"})


def corrections(**changes):
    return replace(
        CardCorrections(
            "1234,56", "RUB", CashDirection.OUT, "27.10.2026", "14:35", "Вымышленный пример"
        ),
        **changes,
    )


def test_card_fields_are_exact_with_word_evidence_and_no_operation_kind():
    value = card()
    assert not value.blocked_reason
    assert value.amount.value == 123456 and value.currency.value == "RUB"
    assert value.direction.value == CashDirection.OUT
    assert value.occurred_on.value == date(2026, 10, 27)
    assert value.local_time.value == time(14, 35)
    assert value.description.value == "Вымышленный пример"
    assert value.amount.word_indices == (2,)
    assert value.confidence(value.amount) == Decimal(96)
    assert "1234" not in repr(value) and "Вымышленный" not in repr(value.description)


@pytest.mark.parametrize(
    "amount,minor,direction",
    [
        ("+5 000,01 ₽", 500001, CashDirection.IN),
        ("−1\u202f234,56 руб.", 123456, CashDirection.OUT),
        ("Сумма: -1\u00a0234,56 RUB", 123456, CashDirection.OUT),
        ("-12 RUB", 1200, CashDirection.OUT),
    ],
)
def test_exact_money_formats_without_float(amount, minor, direction):
    value = card((*LINES[:2], amount, *LINES[3:]))
    assert value.amount.value == minor and value.direction.value == direction
    assert value.currency.value == "RUB"


@pytest.mark.parametrize("amount", ["-1234,56 Р", "-1234,56 P", "-1234,56"])
def test_confusable_or_missing_currency_is_never_assumed_rub(amount):
    value = card((*LINES[:2], amount, *LINES[3:]))
    assert value.amount.value == 123456 and value.currency.value is None
    assert OcrIssue.UNRECOGNIZED in value.currency.issues
    assert value.currency.word_indices == (2,)


@pytest.mark.parametrize("amount", ["1234,56 RUB", "—1234,56 RUB", "–1234,56 RUB"])
def test_missing_or_ambiguous_sign_is_not_inferred_from_title(amount):
    value = card((*LINES[:2], amount, *LINES[3:]))
    assert value.amount.value == 123456 and value.direction.value is None


@pytest.mark.parametrize("text", ["-О,50 RUB", "-1 23,45 RUB", "-1,234 RUB", "-1e5 RUB"])
def test_letter_digit_correction_bad_grouping_or_fraction_is_not_accepted(text):
    value = card((*LINES[:2], text, *LINES[3:]))
    assert value.amount.value is None


@pytest.mark.parametrize("text", ["-0,00 RUB", "-" + "9" * 30 + " RUB"])
def test_zero_or_overflow_is_a_field_error_without_echo(text):
    value = card((*LINES[:2], text, *LINES[3:]))
    assert value.amount.value is None and OcrIssue.INVALID in value.amount.issues


@pytest.mark.parametrize("text", ["27 октября 14:35", "27 октября"])
def test_missing_year_never_uses_current_year(text):
    value = card((*LINES[:3], text, *LINES[4:]))
    assert value.occurred_on.value is None and OcrIssue.MISSING in value.occurred_on.issues
    assert value.occurred_on.word_indices == (3,)


def test_invalid_calendar_time_and_multiple_dates_remain_unresolved():
    value = card((*LINES[:3], "31.02.2026 25:10", *LINES[4:]))
    assert value.occurred_on.value is None and value.local_time.value is None
    assert OcrIssue.INVALID in value.occurred_on.issues
    value = card((*LINES, "26.10.2026 10:00"))
    assert value.occurred_on.value is None and OcrIssue.AMBIGUOUS in value.occurred_on.issues


def test_phone_status_bar_time_is_not_the_operation_time():
    value = card(("15:44", *LINES[:3], "27.10.2026", *LINES[4:]))
    assert value.local_time.value is None and value.occurred_on.value == date(2026, 10, 27)


@pytest.mark.parametrize(
    "lines",
    [
        ("Неизвестный банк", "-1 234,56 RUB", "27.10.2026"),
        (*LINES, "+50,00 RUB"),
        (*LINES, "Комиссия: 5,00 RUB"),
        (*LINES, "История операций"),
        (*LINES, "Перевод", "+20,00 RUB"),
        (*LINES, "Операция отменена"),
        (*LINES, "В обработке"),
    ],
)
def test_unknown_multiple_fee_history_or_pending_card_is_blocked(lines):
    value = card(lines)
    assert value.blocked_reason
    with pytest.raises(ValidationError):
        review_card(
            value, corrections(), confirmed_fields=CONFIRMED, single_completed_operation=True
        )


def test_low_confidence_is_visible_even_when_text_parses():
    value = card(confidence=Decimal("79.9"))
    assert value.amount.value == 123456
    assert OcrIssue.LOW_CONFIDENCE in value.amount.issues


def test_review_requires_every_explicit_confirmation_and_does_not_change_source():
    source = card()
    for names, single in [
        (frozenset(), True),
        (CONFIRMED - {"currency"}, True),
        (CONFIRMED, False),
    ]:
        with pytest.raises(ValidationError):
            review_card(
                source, corrections(), confirmed_fields=names, single_completed_operation=single
            )
    result = review_card(
        source, corrections(), confirmed_fields=CONFIRMED, single_completed_operation=True
    )
    assert result.amount.minor == 123456 and result.changed_fields == ()
    assert result.source is source and result.direction == CashDirection.OUT


def test_manual_correction_records_changed_fields_and_preserves_ocr_evidence():
    source = card((*LINES[:2], "1234,56 Р", "27 октября", *LINES[4:]))
    result = review_card(
        source,
        corrections(amount="1234,57"),
        confirmed_fields=CONFIRMED,
        single_completed_operation=True,
    )
    assert result.amount.minor == 123457
    assert set(result.changed_fields) == {
        "amount",
        "currency",
        "direction",
        "occurred_on",
        "local_time",
    }
    assert result.source.currency.value is None and source.occurred_on.value is None


@pytest.mark.parametrize(
    "changes",
    [
        {"amount": "-1"},
        {"amount": "1e2"},
        {"amount": "NaN"},
        {"amount": "1,001"},
        {"amount": "0"},
        {"amount": "1 23"},
        {"currency": "USD"},
        {"direction": None},
        {"occurred_on": "27.10.26"},
        {"occurred_on": "31.02.2026"},
        {"local_time": "25:01"},
        {"description": "непечатаемое\nполе"},
    ],
)
def test_manual_input_is_validated_without_financial_values_in_error(changes):
    with pytest.raises(ValidationError):
        review_card(
            card(),
            corrections(**changes),
            confirmed_fields=CONFIRMED,
            single_completed_operation=True,
        )


def test_card_application_rejects_adapter_from_different_document(tmp_path):
    class Engine:
        def recognize(self, value):
            return document()

    value = LoadedOcrImage("3" * 64, image())
    service = CardRecognition(Ocr(Engine()), lambda path: value, TBankCardAdapter())
    result = service.preview(tmp_path / "unused")
    assert result.card.amount.value == 123456 and result.source is value

    class Wrong:
        def parse(self, doc):
            return card(image_sha256="f" * 64)

    with pytest.raises(ValidationError, match="другого"):
        CardRecognition(Ocr(Engine()), lambda path: value, Wrong()).preview(tmp_path / "unused")


@pytest.mark.parametrize(
    "field,value",
    [
        ("amount", 1.5),
        ("amount", True),
        ("amount", 0),
        ("currency", "rub"),
        ("direction", "out"),
        ("occurred_on", "2026-10-27"),
    ],
)
def test_field_values_cannot_bypass_typed_card_boundary(field, value):
    from personal_finance.domain.ocr_cards import OcrField

    with pytest.raises(ValidationError):
        replace(card(), **{field: OcrField(value)})


def test_cancelled_word_in_description_is_not_a_transaction_status():
    value = card((*LINES[:4], "Описание: Отменённый заказ"))
    assert not value.blocked_reason


def test_field_evidence_cannot_refer_outside_document():
    from personal_finance.domain.ocr_cards import OcrField

    with pytest.raises(ValidationError):
        replace(card(), amount=OcrField(123456, (999,)))
