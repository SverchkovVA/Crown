"""Явные подписи направления и сохранение частичных полей без записи денег."""

from datetime import date, time
from decimal import Decimal

import pytest

from personal_finance.domain.ocr_cards import CashDirection, OcrIssue
from personal_finance.infrastructure.ocr.generic import GenericCardAdapter
from tests.ocr_fixtures import document


@pytest.mark.parametrize(
    "label,expected",
    [
        ("Счёт списания", CashDirection.OUT),
        ("Карта списания:", CashDirection.OUT),
        ("Счёт зачисления", CashDirection.IN),
        ("Карта зачисления:", CashDirection.IN),
    ],
)
def test_unique_explicit_account_role_can_propose_missing_direction(label, expected):
    source = document(("Детали операции", "123,45 RUB", label, "Тестовый счёт", "23.10.2026"))
    card = GenericCardAdapter().parse(source)
    assert not card.blocked_reason and card.amount.value == 12345
    assert card.direction.value == expected
    assert card.direction.word_indices == (2,)
    assert source.words[2].text == label


@pytest.mark.parametrize("sign,expected", [("+", CashDirection.IN), ("−", CashDirection.OUT)])
def test_known_sign_is_not_replaced_by_account_role(sign, expected):
    card = GenericCardAdapter().parse(
        document(("Детали операции", sign + "123,45 RUB", "Счёт списания", "23.10.2026"))
    )
    assert not card.blocked_reason and card.direction.value == expected
    assert card.direction.word_indices == card.amount.word_indices


@pytest.mark.parametrize("amount", ["123,45 RUB", "—123,45 RUB"])
def test_both_account_roles_do_not_choose_a_transfer_side(amount):
    card = GenericCardAdapter().parse(
        document(
            (
                "Детали операции",
                amount,
                "Счёт списания",
                "Тестовый первый",
                "Счёт зачисления",
                "Тестовый второй",
                "23.10.2026",
            )
        )
    )
    assert not card.blocked_reason and card.direction.value is None
    from personal_finance.application.ocr_review import OcrReviewDefaults, suggest_corrections

    assert suggest_corrections(card, OcrReviewDefaults()).direction is None


@pytest.mark.parametrize("label", ["Счёт отправителя", "Счёт получателя", "Переводы людям"])
def test_party_role_and_title_do_not_prove_direction(label):
    card = GenericCardAdapter().parse(
        document(("Детали операции", "123,45 RUB", label, "23.10.2026"))
    )
    assert not card.blocked_reason and card.direction.value is None


def test_low_confidence_account_role_keeps_warning_with_word_evidence():
    card = GenericCardAdapter().parse(
        document(
            ("Детали операции", "123,45 RUB", "Счёт списания", "23.10.2026"),
            confidence=Decimal("72"),
        )
    )
    assert card.direction.value == CashDirection.OUT
    assert OcrIssue.LOW_CONFIDENCE in card.direction.issues


def test_missing_amount_keeps_date_time_currency_and_description():
    source = document(
        (
            "Детали операции",
            "Сумма не распознана",
            "Валюта: RUB",
            "Дата операции: 23.10.2026 в 19:17",
            "Описание: Учебный пример",
        )
    )
    card = GenericCardAdapter().parse(source)
    assert card.blocked_reason and card.amount.value is None
    assert card.occurred_on.value == date(2026, 10, 23)
    assert card.local_time.value == time(19, 17)
    assert card.currency.value == "RUB"
    assert card.description.value == "Учебный пример"
    assert card.document is source


def test_account_role_in_partial_card_never_makes_balance_the_operation_amount():
    card = GenericCardAdapter().parse(
        document(
            (
                "Детали операции",
                "Счёт списания",
                "Баланс: 9 876,54 RUB",
                "Дата операции: 23.10.2026",
            )
        )
    )
    assert card.blocked_reason and card.amount.value is None
    assert card.occurred_on.value == date(2026, 10, 23)
    assert card.direction.value == CashDirection.OUT


def test_conflicting_primary_label_and_sign_still_block_the_card():
    card = GenericCardAdapter().parse(
        document(("Детали операции", "Зачислено: -123,45 RUB", "Счёт списания", "23.10.2026"))
    )
    assert card.blocked_reason
