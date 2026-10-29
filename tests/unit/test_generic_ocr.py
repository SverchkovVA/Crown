from datetime import date, time
from decimal import Decimal

import pytest

from personal_finance.application.ocr_review import OcrReviewDefaults, suggest_corrections
from personal_finance.domain.ocr import OcrBox, OcrDocument, OcrWord
from personal_finance.domain.ocr_cards import CashDirection, OcrIssue
from personal_finance.infrastructure.ocr.generic import GenericCardAdapter, GenericHistoryAdapter
from personal_finance.infrastructure.ocr.generic_dates import parse_date
from tests.ocr_fixtures import ENGINE, document


def layout(rows):
    words = []
    for index, (text, left, top) in enumerate(rows):
        for number, token in enumerate(text.split(), 1):
            words.append(
                OcrWord(
                    token,
                    OcrBox(left, top, max(9, len(token) * 8), 28),
                    Decimal(96),
                    (1, index + 1, 1),
                    number,
                )
            )
            left += max(9, len(token) * 8) + 8
    return OcrDocument("b" * 64, 1080, 1400, ENGINE, tuple(words))


@pytest.mark.parametrize(
    "text,expected",
    [
        ("03.01.2026", date(2026, 1, 3)),
        ("03/01/2026", date(2026, 1, 3)),
        ("2026-01-03", date(2026, 1, 3)),
        ("3.1", None),
        ("3 янв.", None),
        ("Пн, 3 января 2026 в 08:17", date(2026, 1, 3)),
        ("Сегодня, 3 января", None),
    ],
)
def test_general_dates_keep_missing_year_explicit(text, expected):
    parts = parse_date(text)
    assert parts is not None and parts.calendar() == expected
    if expected is None:
        assert parts.calendar(2026) == date(2026, 1, 3)


@pytest.mark.parametrize(
    "amount,minor,direction,currency",
    [
        ("-1 234,56 RUB", 123456, CashDirection.OUT, "RUB"),
        ("+1234.5 ₽", 123450, CashDirection.IN, "RUB"),
        ("−1 234,56", 123456, CashDirection.OUT, None),
        ("−123,4 Р", 12340, CashDirection.OUT, None),
        ("123,40 руб.", 12340, None, "RUB"),
        ("Сумма: 1234", 123400, None, None),
        ("Списано: 123,40 RUB", 12340, CashDirection.OUT, "RUB"),
        ("Зачислено 123,40 RUB", 12340, CashDirection.IN, "RUB"),
    ],
)
def test_card_fields_do_not_depend_on_bank_name(amount, minor, direction, currency):
    card = GenericCardAdapter().parse(
        document(("Банк вымышленный", "Детали операции", amount, "03/01/2026 08:17"))
    )
    assert not card.blocked_reason
    assert card.amount.value == minor and card.direction.value == direction
    assert card.currency.value == currency and card.local_time.value == time(8, 17)
    assert card.occurred_on.value == date(2026, 1, 3)


def test_partial_numeric_date_uses_settings_only_in_proposal():
    card = GenericCardAdapter().parse(document(("Покупка", "-123,40 RUB", "03.01")))
    assert card.occurred_on.value is None
    assert (
        suggest_corrections(card, OcrReviewDefaults(reference_year=2026)).occurred_on
        == "03.01.2026"
    )


@pytest.mark.parametrize(
    "text", ["-1 23,45 RUB", "-1,234 RUB", "-О,50 RUB", "Телефон +79991234567", "Номер 1234"]
)
def test_wrong_money_or_details_never_become_a_card_amount(text):
    card = GenericCardAdapter().parse(document(("Детали операции", text, "03.01.2026")))
    assert card.amount.value is None


@pytest.mark.parametrize(
    "extra", ["В обработке", "Статус: Отменена", "Черновик", "Комиссия: 10 RUB"]
)
def test_card_pending_and_separate_fee_are_blocked(extra):
    card = GenericCardAdapter().parse(document(("Покупка", "−123,4 RUB", "03.01.2026", extra)))
    assert card.blocked_reason


@pytest.mark.parametrize(
    "value,currency", [("+12 USD", "USD"), ("12 EUR", "EUR"), ("-12,50 €", "EUR")]
)
def test_known_foreign_currency_cannot_become_default_rub(value, currency):
    card = GenericCardAdapter().parse(document(("Покупка", value, "03.01.2026")))
    assert card.blocked_reason and card.currency.value == currency


def test_summary_and_cashback_metadata_do_not_replace_main_amount():
    card = GenericCardAdapter().parse(
        document(("Покупка", "-123,40 RUB", "03.01.2026", "Доступно 9999 RUB", "Кешбэк 5 RUB"))
    )
    assert not card.blocked_reason and card.amount.value == 12340


def test_balance_screen_without_operation_context_is_not_a_card():
    card = GenericCardAdapter().parse(document(("Мои счета", "9999 RUB", "03.01.2026")))
    assert card.blocked_reason


def test_history_joins_visual_columns_even_when_engine_line_ids_differ():
    source = layout(
        [
            ("История", 40, 20),
            ("3 января", 40, 90),
            ("Вымышленный магазин", 40, 160),
            ("−123,40 RUB", 730, 160),
            ("Другой пример", 40, 230),
            ("+50 RUB", 730, 230),
        ]
    )
    result = GenericHistoryAdapter().parse(source)
    assert not result.blocked_reason and len(result.cards) == 2
    assert [c.amount.value for c in result.cards] == [12340, 5000]
    assert all(
        suggest_corrections(c, OcrReviewDefaults(reference_year=2026)).occurred_on == "03.01.2026"
        for c in result.cards
    )
    assert result.cards[0].description.value == "Вымышленный магазин"


@pytest.mark.parametrize("money_left", [40, 750])
def test_inline_dates_and_amounts_in_different_columns(money_left):
    source = layout(
        [
            ("История операций", 40, 20),
            ("03/01/2026 Магазин", 300 if money_left == 40 else 40, 100),
            ("−12,50 RUB", money_left, 100),
            ("2026-01-04 Другой пример", 300 if money_left == 40 else 40, 180),
            ("+14,20 RUB", money_left, 180),
        ]
    )
    result = GenericHistoryAdapter().parse(source)
    assert not result.blocked_reason and len(result.cards) == 2
    assert [c.occurred_on.value for c in result.cards] == [date(2026, 1, 3), date(2026, 1, 4)]


def test_history_has_no_bank_specific_heading_requirement():
    source = layout(
        [
            ("03.01", 40, 80),
            ("Магазин", 40, 150),
            ("−12.50", 750, 150),
            ("Другой пример", 40, 240),
            ("+14.20", 750, 240),
        ]
    )
    result = GenericHistoryAdapter().parse(source)
    assert not result.blocked_reason and len(result.cards) == 2
    assert all(c.occurred_on.value is None for c in result.cards)
    assert all(
        suggest_corrections(c, OcrReviewDefaults(reference_year=2026)).occurred_on == "03.01.2026"
        for c in result.cards
    )


def test_date_total_is_not_a_separate_operation():
    source = layout(
        [
            ("История", 40, 20),
            ("Сегодня, 3 января", 40, 80),
            ("−12,50 RUB", 750, 80),
            ("Магазин", 40, 150),
            ("−12,50 RUB", 750, 150),
        ]
    )
    result = GenericHistoryAdapter().parse(source)
    assert len(result.cards) == 1 and result.unassigned_fragments == 1


def test_invalid_new_heading_never_inherits_previous_date():
    source = layout(
        [
            ("История", 40, 20),
            ("03.01.2026", 40, 80),
            ("Магазин −12,50 RUB", 40, 150),
            ("31/02/2026", 40, 220),
            ("Другой пример −14,20 RUB", 40, 290),
        ]
    )
    result = GenericHistoryAdapter().parse(source)
    assert len(result.cards) == 2
    assert result.cards[0].occurred_on.value == date(2026, 1, 3)
    assert (
        result.cards[1].occurred_on.value is None
        and OcrIssue.INVALID in result.cards[1].occurred_on.issues
    )


def test_history_unsigned_amount_does_not_guess_a_cash_direction():
    source = layout(
        [("История", 40, 20), ("03.01.2026", 40, 80), ("Магазин", 40, 150), ("12,50 RUB", 750, 150)]
    )
    result = GenericHistoryAdapter().parse(source)
    assert len(result.cards) == 1 and result.cards[0].direction.value is None


def test_pending_status_belongs_to_its_row_not_the_whole_history():
    source = layout(
        [
            ("История", 40, 20),
            ("03.01.2026", 40, 80),
            ("Магазин −12,50 RUB", 40, 150),
            ("В обработке", 40, 185),
            ("Другой пример +14,20 RUB", 40, 300),
        ]
    )
    result = GenericHistoryAdapter().parse(source)
    assert not result.blocked_reason and len(result.cards) == 2
    assert result.cards[0].blocked_reason and not result.cards[1].blocked_reason


def test_missing_context_never_makes_a_budget_list_into_operations():
    source = layout([("Планы", 40, 20), ("Продукты 5000 RUB", 40, 100), ("Дом 7000 RUB", 40, 200)])
    assert GenericHistoryAdapter().parse(source).blocked_reason


@pytest.mark.parametrize(
    "label,direction",
    [("Сумма операции", None), ("Списано", CashDirection.OUT), ("Зачислено", CashDirection.IN)],
)
def test_amount_label_and_value_on_separate_rows(label, direction):
    source = layout(
        [
            ("Детали операции", 40, 30),
            (label, 40, 100),
            ("1234,50", 40, 140),
            ("03.01.2026", 40, 230),
        ]
    )
    card = GenericCardAdapter().parse(source)
    assert (
        not card.blocked_reason
        and card.amount.value == 123450
        and card.direction.value == direction
    )
    assert card.occurred_on.value == date(2026, 1, 3)


@pytest.mark.parametrize("currency", ["USD", "EUR", "Валюта операции: USD"])
def test_separate_foreign_currency_cannot_become_default_rub(currency):
    card = GenericCardAdapter().parse(document(("Покупка", "-123,40", "03.01.2026", currency)))
    assert card.blocked_reason and card.currency.value in ("USD", "EUR")


def test_history_currency_below_the_amount_is_preserved():
    source = layout(
        [("История", 40, 20), ("03.01.2026", 40, 80), ("Магазин -12,50", 40, 160), ("USD", 40, 200)]
    )
    result = GenericHistoryAdapter().parse(source)
    assert (
        len(result.cards) == 1
        and result.cards[0].currency.value == "USD"
        and result.cards[0].blocked_reason
    )


def test_card_repeated_equal_labelled_amount_is_one_source_with_both_regions():
    source = layout(
        [
            ("Платёж", 40, 20),
            ("-123,40 RUB", 40, 100),
            ("Сумма операции: 123,40 RUB", 40, 180),
            ("03.01.2026", 40, 260),
        ]
    )
    card = GenericCardAdapter().parse(source)
    assert not card.blocked_reason and card.amount.value == 12340
    assert card.direction.value == CashDirection.OUT and len(card.amount.word_indices) >= 4


@pytest.mark.parametrize(
    "second",
    ["Сумма операции: 123,41 RUB", "Сумма операции: +123,40 RUB", "Сумма операции: 123,40 USD"],
)
def test_disagreeing_repeated_amounts_are_never_merged(second):
    card = GenericCardAdapter().parse(document(("Платёж", "-123,40 RUB", second, "03.01.2026")))
    assert card.blocked_reason


@pytest.mark.parametrize("status", ["В обработке", "Отклонено", "Статус: Отменена"])
def test_unbound_pending_status_blocks_history_instead_of_being_lost(status):
    source = layout(
        [
            ("История", 40, 20),
            (status, 40, 80),
            ("03.01.2026", 40, 350),
            ("Магазин -12,50 RUB", 40, 500),
        ]
    )
    assert GenericHistoryAdapter().parse(source).blocked_reason


def test_unbound_fee_is_not_lost_before_the_first_operation():
    source = layout(
        [
            ("История", 40, 20),
            ("Комиссия: 10 RUB", 40, 80),
            ("03.01.2026", 40, 200),
            ("Магазин -12,50 RUB", 40, 300),
        ]
    )
    assert GenericHistoryAdapter().parse(source).blocked_reason


def test_direction_label_and_explicit_sign_cannot_contradict_each_other():
    card = GenericCardAdapter().parse(document(("Платёж", "Зачислено: -123,40 RUB", "03.01.2026")))
    assert card.blocked_reason


@pytest.mark.parametrize("description", ["Входящий перевод", "Исходящий перевод"])
def test_transfer_description_is_not_confused_with_opening_or_closing_balance(description):
    source = layout(
        [("История", 40, 20), ("03.01.2026", 40, 80), (description + " -12,50 RUB", 40, 160)]
    )
    result = GenericHistoryAdapter().parse(source)
    assert len(result.cards) == 1 and result.cards[0].amount.value == 1250


def test_thousand_grouping_never_truncates_money_property():
    from hypothesis import given, settings
    from hypothesis import strategies as st

    from personal_finance.domain.models import MAX_MINOR

    @settings(max_examples=100)
    @given(st.integers(min_value=1, max_value=MAX_MINOR), st.booleans())
    def check(minor, currency):
        formatted = (
            "-"
            + f"{minor // 100:,}".replace(",", " ")
            + f",{minor % 100:02d}"
            + (" RUB" if currency else "")
        )
        card = GenericCardAdapter().parse(document(("Покупка", formatted, "03.01.2026")))
        assert card.amount.value == minor

    check()


@pytest.mark.parametrize(
    "value", ["24октября", "24октября2026", "24 окт.2026", "24окт2026", "24октября 2026 в 13:42"]
)
def test_known_month_without_ocr_spaces_keeps_explicit_calendar(value):
    parts = parse_date(value)
    assert parts is not None and parts.calendar(2026) == date(2026, 10, 24)
    card = GenericCardAdapter().parse(document(("Покупка", "-12,50 RUB", value)))
    assert (
        suggest_corrections(card, OcrReviewDefaults(reference_year=2026)).occurred_on
        == "24.10.2026"
    )


@pytest.mark.parametrize("value", ["124октября", "24октября26", "24октября20261", "24октябрв2026"])
def test_compact_month_format_does_not_repair_digits_or_letters(value):
    assert parse_date(value) is None
    card = GenericCardAdapter().parse(document(("Покупка", "-12,50 RUB", value)))
    assert not suggest_corrections(card, OcrReviewDefaults(reference_year=2026)).occurred_on


@pytest.mark.parametrize(
    "currency", ["рубль", "рубля", "рублей", "РУБЛЕЙ", "Рубль", "руб.", "RUB", "₽"]
)
def test_card_exact_russian_currency_forms(currency):
    card = GenericCardAdapter().parse(
        document(
            ("Детали операции", "Списано: 473,15 " + currency, "Дата операции: 22.10.2026 09:18")
        )
    )
    assert not card.blocked_reason
    assert (
        card.amount.value,
        card.currency.value,
        card.direction.value,
        card.occurred_on.value,
        card.local_time.value,
    ) == (47315, "RUB", CashDirection.OUT, date(2026, 10, 22), time(9, 18))
    assert card.adapter == "generic-card-v2"
    assert card.currency.word_indices and card.amount.word_indices
    assert any(currency in card.document.words[i].text for i in card.currency.word_indices)


@pytest.mark.parametrize("currency", ["рубль", "рубля", "рублей", "РУБЛЕЙ"])
def test_russian_currency_hint_keeps_number_and_provenance(currency):
    card = GenericCardAdapter().parse(
        document(
            (
                "Детали операции",
                "Списано: 473,15",
                "Валюта операции: " + currency,
                "22.10.2026 09:18",
            )
        )
    )
    assert not card.blocked_reason and card.amount.value == 47315 and card.currency.value == "RUB"
    assert any(currency in card.document.words[i].text for i in card.currency.word_indices)


@pytest.mark.parametrize("currency", ["рубль", "рубля", "рублей", "РУБЛЕЙ"])
def test_history_currency_forms_and_date_header(currency):
    value = GenericHistoryAdapter().parse(
        layout(
            [
                ("История", 40, 20),
                ("22 октября 2026", 40, 90),
                ("Вымышленный магазин", 40, 160),
                ("−473,15 " + currency, 700, 160),
                ("Другой пример", 40, 260),
                ("+20 " + currency, 700, 260),
            ]
        )
    )
    assert not value.blocked_reason and len(value.cards) == 2
    assert [c.amount.value for c in value.cards] == [47315, 2000]
    assert [c.direction.value for c in value.cards] == [CashDirection.OUT, CashDirection.IN]
    assert all(
        c.currency.value == "RUB"
        and c.occurred_on.value == date(2026, 10, 22)
        and c.adapter == "generic-history-v2"
        for c in value.cards
    )


@pytest.mark.parametrize(
    "extra", ["Валюта: USD", "Валюта: EUR", "Комиссия: 10 рублей", "Статус: В обработке"]
)
def test_currency_forms_do_not_remove_foreign_fee_or_pending_guards(extra):
    card = GenericCardAdapter().parse(
        document(("Детали операции", "Списано: 473,15 рублей", "22.10.2026", extra))
    )
    assert card.blocked_reason


@pytest.mark.parametrize("suffix", ["рубл", "рублейX", "рубля2", "ВОВ", "баллов", "%"])
def test_unsigned_label_does_not_guess_incomplete_or_unknown_currency(suffix):
    card = GenericCardAdapter().parse(
        document(("Детали операции", "Списано: 473,15 " + suffix, "22.10.2026"))
    )
    assert card.amount.value is None and card.blocked_reason


@pytest.mark.parametrize(
    "line", ["Баланс 473,15 рублей", "Доступно 473,15 рублей", "Кешбэк 473,15 рублей"]
)
def test_metadata_does_not_replace_primary_amount(line):
    card = GenericCardAdapter().parse(
        document(("Детали операции", "Списано: 12,50 рубля", "22.10.2026", line))
    )
    assert card.amount.value == 1250 and not card.blocked_reason
