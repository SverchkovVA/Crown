"""Статусные часы и частичные карточки: синтетические примеры без банковских данных."""

from datetime import date, time
from pathlib import Path

import pytest

from personal_finance.application.ocr import Ocr
from personal_finance.application.ocr_cards import CardRecognition
from personal_finance.domain.errors import ValidationError
from personal_finance.domain.ocr import LoadedOcrImage
from personal_finance.domain.ocr_cards import CardCorrections, CashDirection, review_card
from personal_finance.domain.ocr_profiles import UNIVERSAL_PROFILE
from personal_finance.infrastructure.ocr.generic import GenericCardAdapter, GenericHistoryAdapter
from tests.ocr_fixtures import document, image
from tests.unit.test_ocr_card_fallback import Engine


@pytest.mark.parametrize("clock", ["05:10 P", "05: 10 Р", "05 : 10 ₽", "12:34:56P", "25:99 Р"])
def test_clock_fragment_is_not_a_money_operation(clock):
    result = GenericHistoryAdapter().parse(
        document(
            (
                clock,
                "История операций",
                "27 октября 2026",
                "Тестовый магазин -45,70 RUB",
                "Тестовый перевод +120,00 RUB",
            )
        )
    )
    assert len(result.cards) == 2
    assert [card.amount.value for card in result.cards] == [4570, 12000]
    assert [card.direction.value for card in result.cards] == [CashDirection.OUT, CashDirection.IN]
    assert not result.blocked_reason


def test_real_money_after_time_on_same_row_is_preserved():
    result = GenericHistoryAdapter().parse(
        document(("История операций", "27 октября 2026", "05:10 Тестовый магазин -45,70 RUB"))
    )
    assert len(result.cards) == 1 and result.cards[0].amount.value == 4570


def test_clock_icon_does_not_block_single_card_with_real_amount():
    result = GenericCardAdapter().parse(
        document(("05:10 P", "Детали операции", "-45,70 RUB", "27.10.2026 18:40"))
    )
    assert not result.blocked_reason and result.amount.value == 4570
    assert result.local_time.value == time(18, 40)


@pytest.mark.parametrize(
    "label", ["Дата и время", "Дата операции:", "Дата перевода", "Дата проведения"]
)
def test_explicit_operation_date_below_account_section_is_used(label):
    result = GenericCardAdapter().parse(
        document(
            (
                "Детали операции",
                "-12,34 RUB",
                "Счёт списания",
                "Основной тестовый",
                "9 000,00 RUB",
                label,
                "23.10.2026 в 19:17",
            )
        )
    )
    assert not result.blocked_reason and result.amount.value == 1234
    assert result.occurred_on.value == date(2026, 10, 23)
    assert result.local_time.value == time(19, 17)


def test_unlabelled_date_inside_account_section_is_not_operation_date():
    result = GenericCardAdapter().parse(
        document(
            (
                "Детали операции",
                "-12,34 RUB",
                "Счёт списания",
                "Основной тестовый",
                "Срок действия",
                "23.10.2026",
            )
        )
    )
    assert not result.blocked_reason and result.amount.value == 1234
    assert result.occurred_on.value is None


def recognition(base, alternate):
    loaded = LoadedOcrImage("3" * 64, image())
    first, second = Engine(document(base)), Engine(document(alternate))
    service = CardRecognition(
        Ocr(first),
        lambda _: loaded,
        GenericCardAdapter(),
        bank_name=UNIVERSAL_PROFILE,
        fallback_ocr=Ocr(second),
        retry_missing_card=GenericCardAdapter.can_retry,
    )
    return service, loaded, first, second


def test_fallback_can_show_found_amount_while_date_stays_explicitly_missing():
    service, loaded, first, second = recognition(
        ("Детали операции", "Сумма не распознана"),
        ("Детали операции", "-12,34 RUB"),
    )
    result = service.preview(Path("unused.png"))
    assert first.calls == second.calls == 1
    assert result.source is loaded and result.card.document.words == second.result.words
    assert not result.card.blocked_reason and result.card.amount.value == 1234
    assert result.card.occurred_on.value is None
    assert not result.card.occurred_on.word_indices
    with pytest.raises(ValidationError):
        review_card(
            result.card,
            CardCorrections("12,34", "RUB", CashDirection.OUT, "", "", ""),
            confirmed_fields=frozenset({"amount", "currency", "direction", "occurred_on"}),
            single_completed_operation=True,
        )


def test_partial_fallback_never_drops_known_primary_date():
    from tests.unit.test_ocr_card_fallback import service as legacy_service

    service, _, second = legacy_service(
        ("Т-Банк", "Перевод", "Сумма не распознана", "23.10.2026"),
        ("Т-Банк", "Перевод", "-12,34 RUB"),
    )
    result = service.preview(Path("unused.png"))
    assert second.calls == 1
    assert result.card.amount.value is None
    assert result.card.occurred_on.value == date(2026, 10, 23)
