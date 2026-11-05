from dataclasses import replace
from pathlib import Path

import pytest

from personal_finance.application.ocr import Ocr
from personal_finance.application.ocr_cards import CardRecognition
from personal_finance.domain.ocr import LoadedOcrImage
from personal_finance.infrastructure.ocr.t_bank import TBankCardAdapter
from tests.ocr_fixtures import LINES, document, image


class Engine:
    def __init__(self, result):
        self.result = result
        self.calls = 0

    def recognize(self, source):
        self.calls += 1
        return replace(
            self.result, image_sha256=source.sha256, width=source.width, height=source.height
        )


def service(base, alternate):
    first, second = Engine(document(base)), Engine(document(alternate))
    loaded = LoadedOcrImage("3" * 64, image())
    return (
        CardRecognition(Ocr(first), lambda _: loaded, TBankCardAdapter(), fallback_ocr=Ocr(second)),
        first,
        second,
    )


def test_missing_amount_can_use_complete_alternate_document_without_merging_words():
    value, first, second = service((*LINES[:2], "Не распознано", *LINES[3:]), LINES)
    result = value.preview(Path("unused.png"))
    assert result.card.amount.value == 123456 and second.calls == 1
    assert result.card.document.words == document().words


def test_complete_primary_card_never_runs_second_engine():
    value, _, second = service(LINES, LINES)
    assert value.preview(Path("unused.png")).card.amount.value == 123456
    assert second.calls == 0


@pytest.mark.parametrize(
    "bad", [("Т-Банк", "Перевод", "-1,00 RUB", "-2,00 RUB"), (*LINES, "В обработке")]
)
def test_blocked_or_pending_primary_is_not_replaced_by_a_simpler_alternate(bad):
    value, _, second = service(bad, LINES)
    assert value.preview(Path("unused.png")).card.blocked_reason
    assert second.calls == 0


def test_alternate_cannot_replace_known_amount_with_conflicting_value():
    base = (*LINES[:3], "Дата не распознана")
    alternate = (*LINES[:2], "-9 876,54 RUB", *LINES[3:])
    value, _, second = service(base, alternate)
    result = value.preview(Path("unused.png"))
    assert second.calls == 1 and result.card.amount.value == 123456
    assert result.card.occurred_on.value is None


def test_alternate_does_not_drop_a_known_currency_or_direction():
    base = (*LINES[:3], "Дата не распознана")
    alternate = (*LINES[:2], "1234,56 Р", *LINES[3:])
    value, _, second = service(base, alternate)
    result = value.preview(Path("unused.png"))
    assert second.calls == 1 and result.card.document.words == document(base).words
