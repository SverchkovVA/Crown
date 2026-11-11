"""Разделение источника суммы и соседнего короткого слова."""

from decimal import Decimal

import pytest

from personal_finance.application.ocr import Ocr
from personal_finance.domain.ocr import OcrBox, OcrDocument, OcrWord
from personal_finance.domain.ocr_cards import OcrIssue
from personal_finance.infrastructure.ocr.amount_reread import HistoryAmountReread
from personal_finance.infrastructure.ocr.generic import GenericHistoryAdapter
from tests.ocr_fixtures import ENGINE, png_image
from tests.unit.test_ocr_amount_reread import RegionEngine


def dated_example(*, suffix_left=386, confidence=51, merchant=False, separate_date=False):
    image = png_image()
    words = (
        OcrWord("История операций", OcrBox(20, 30, 170, 20), Decimal(95), (1, 1, 1), 1),
        OcrWord(
            "27 октября 2026",
            OcrBox(18, 86 if separate_date else 110, 95, 13),
            Decimal(91),
            (1, 1, 2),
            1,
        ),
        OcrWord("—72550", OcrBox(345, 112, 38, 9), Decimal(17), (1, 1, 2), 2),
        OcrWord("ВОВ", OcrBox(suffix_left, 112, 23, 8), Decimal(confidence), (1, 1, 2), 3),
        OcrWord("26.10.2026", OcrBox(20, 135, 95, 12), Decimal(95), (1, 1, 4), 1),
        OcrWord("Учебная покупка", OcrBox(20, 155, 110, 12), Decimal(95), (1, 1, 3), 1),
        OcrWord("−40,00 RUB", OcrBox(345, 155, 65, 12), Decimal(95), (1, 1, 3), 2),
    )
    if merchant:
        words += (OcrWord("Учебный магазин", OcrBox(150, 112, 105, 9), Decimal(95), (1, 1, 2), 4),)
    return image, OcrDocument(image.sha256, image.width, image.height, ENGINE, words)


class DatedRegionEngine:
    def __init__(self, amount="—725,50", currency="RUB", *, currency_left=100, merged=False):
        self.amount, self.currency = amount, currency
        self.currency_left, self.merged = currency_left, merged
        self.calls = []

    def recognize(self, image):
        self.calls.append(image)
        if self.merged:
            words = (
                OcrWord(
                    f"{self.amount} {self.currency}",
                    OcrBox(18, 8, 126, 16),
                    Decimal(89),
                    (1, 1, 1),
                    1,
                ),
            )
        else:
            words = (
                OcrWord(
                    self.amount,
                    OcrBox(18, 8, min(74, self.currency_left - 19), 16),
                    Decimal(89),
                    (1, 1, 1),
                    1,
                ),
                OcrWord(
                    self.currency, OcrBox(self.currency_left, 8, 46, 16), Decimal(89), (1, 1, 1), 2
                ),
            )
        return OcrDocument(image.sha256, image.width, image.height, ENGINE, words)


def test_dated_amount_requires_currency_in_the_original_trailing_word_region():
    image, source = dated_example()
    initial = GenericHistoryAdapter().parse(source)
    assert initial.cards[0].description.value == "ВОВ"
    assert initial.cards[0].amount.value == 7255000
    engine = DatedRegionEngine()

    result = HistoryAmountReread(Ocr(engine))(image, initial)

    assert len(engine.calls) == 1
    assert (engine.calls[0].width, engine.calls[0].height) == (164, 34)
    assert result.document is source and result.unassigned_fragments == initial.unassigned_fragments
    assert result.cards[0].amount.value is None
    assert OcrIssue.AMBIGUOUS in result.cards[0].amount.issues
    assert result.cards[0].blocked_reason
    assert result.cards[0].amount.word_indices == initial.cards[0].amount.word_indices
    assert result.cards[0].description == initial.cards[0].description
    assert result.cards[0].occurred_on == initial.cards[0].occurred_on
    assert result.cards[1] is initial.cards[1]


@pytest.mark.parametrize(
    "options",
    [
        {"currency": "ВОВ"},
        {"currency": "P"},
        {"currency": "EUR"},
        {"currency_left": 62},
        {"amount": "—726,50"},
        {"merged": True},
    ],
)
def test_dated_amount_keeps_operation_when_trailing_currency_is_unconfirmed(options):
    image, source = dated_example()
    initial = GenericHistoryAdapter().parse(source)
    engine = DatedRegionEngine(**options)

    assert HistoryAmountReread(Ocr(engine))(image, initial) is initial
    assert len(engine.calls) == 1


@pytest.mark.parametrize(
    "options",
    [{"merchant": True}, {"confidence": 96}, {"separate_date": True}],
)
def test_dated_amount_skips_nonmatching_layouts(options):
    image, source = dated_example(**options)
    initial = GenericHistoryAdapter().parse(source)
    engine = DatedRegionEngine()

    assert HistoryAmountReread(Ocr(engine))(image, initial) is initial
    assert not engine.calls


def test_distant_short_description_stays_outside_the_existing_amount_crop():
    image, source = dated_example(suffix_left=418)
    initial = GenericHistoryAdapter().parse(source)
    engine = RegionEngine("—72550", box=OcrBox(18, 8, 74, 16))

    assert HistoryAmountReread(Ocr(engine))(image, initial) is initial
    assert len(engine.calls) == 1
    assert (engine.calls[0].width, engine.calls[0].height) == (112, 34)
