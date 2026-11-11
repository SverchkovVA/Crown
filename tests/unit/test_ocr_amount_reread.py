"""Перепроверка спорного разделителя и защита координат на вымышленных данных."""

from dataclasses import replace
from decimal import Decimal

import pytest

from personal_finance.application.ocr import Ocr
from personal_finance.application.ocr_cards import CardRecognition, OcrHistoryPreview
from personal_finance.domain.errors import ValidationError
from personal_finance.domain.ocr import LoadedOcrImage, OcrBox, OcrDocument, OcrWord
from personal_finance.domain.ocr_cards import OcrIssue
from personal_finance.domain.ocr_profiles import UNIVERSAL_PROFILE
from personal_finance.infrastructure.ocr.amount_reread import HistoryAmountReread
from personal_finance.infrastructure.ocr.generic import GenericCardAdapter, GenericHistoryAdapter
from tests.ocr_fixtures import ENGINE, png_image


def example(amounts=("−149087", "+50,00 RUB"), *, confidence=70, neighbor=False):
    image = png_image()
    words = [
        OcrWord("История операций", OcrBox(30, 30, 180, 20), Decimal(96), (1, 1, 1), 1),
        OcrWord("27.10.2026", OcrBox(30, 100, 110, 20), Decimal(96), (1, 1, 2), 1),
    ]
    for i, amount in enumerate(amounts):
        y = 160 + i * 80
        words.extend(
            (
                OcrWord("Учебный магазин", OcrBox(45, y, 130, 20), Decimal(96), (1, 1, i + 3), 1),
                OcrWord(amount, OcrBox(660, y, 105, 20), Decimal(confidence), (1, 1, i + 3), 2),
            )
        )
    if neighbor:
        # Соседняя надпись попадает в поля вокруг суммы, но не на ту же строку.
        words.append(OcrWord("Бонусы", OcrBox(745, 182, 60, 20), Decimal(96), (1, 1, 20), 1))
    return image, OcrDocument(image.sha256, image.width, image.height, ENGINE, tuple(words))


class RegionEngine:
    def __init__(self, text="−1 490,87 RUB", *, box=None, fail=False, extra_line=False):
        self.text, self.box, self.fail, self.extra_line = text, box, fail, extra_line
        self.calls = []

    def recognize(self, image):
        self.calls.append(image)
        if self.fail:
            raise ValidationError("Сбой повторного OCR.")
        words = (
            (OcrWord(self.text, self.box or OcrBox(40, 20, 190, 40), Decimal(90), (1, 1, 1), 1),)
            if self.text
            else ()
        )
        if self.extra_line:
            words += (OcrWord("Тест", OcrBox(42, 68, 30, 10), Decimal(90), (1, 1, 2), 1),)
        return OcrDocument(image.sha256, image.width, image.height, ENGINE, words)


def test_matching_digits_with_new_separator_keep_source_and_block_only_that_row():
    image, document = example()
    original = GenericHistoryAdapter().parse(document)
    engine = RegionEngine()
    result = HistoryAmountReread(Ocr(engine))(image, original)
    assert len(engine.calls) == 1
    assert (engine.calls[0].width, engine.calls[0].height) == (290, 80)
    assert engine.calls[0].sha256 != image.sha256
    assert (
        result.document is document and result.unassigned_fragments == original.unassigned_fragments
    )
    assert result.cards[0].amount.value is None and result.cards[0].blocked_reason
    assert OcrIssue.AMBIGUOUS in result.cards[0].amount.issues
    assert result.cards[0].amount.word_indices == original.cards[0].amount.word_indices
    assert result.cards[0].direction == original.cards[0].direction
    assert result.cards[0].occurred_on == original.cards[0].occurred_on
    assert original.cards[0].amount.value == 14908700
    assert result.cards[1] is original.cards[1]


@pytest.mark.parametrize(
    "engine",
    [
        RegionEngine("−149,88 RUB"),  # другой набор цифр
        RegionEngine("+1 490,87 RUB"),  # другой знак
        RegionEngine("−1 490,87 USD"),  # другая валюта
        RegionEngine("−149087 RUB"),  # нет прочитанного разделителя
        RegionEngine(""),  # результата нет
        RegionEngine(box=OcrBox(0, 0, 30, 15)),  # не та позиция
        RegionEngine(extra_line=True),  # несколько строк в области
    ],
)
def test_reread_ignores_unrelated_or_inconclusive_output(engine):
    image, document = example()
    original = GenericHistoryAdapter().parse(document)
    assert HistoryAmountReread(Ocr(engine))(image, original) is original
    assert len(engine.calls) == 1


@pytest.mark.parametrize(
    "options",
    [
        {"confidence": 96},
        {"neighbor": True},
        {"amounts": ("−1490,87",)},
    ],
)
def test_reread_does_not_call_engine_for_clear_or_nonisolated_amount(options):
    image, document = example(**options)
    original = GenericHistoryAdapter().parse(document)
    engine = RegionEngine()
    assert HistoryAmountReread(Ocr(engine))(image, original) is original
    assert not engine.calls


def test_reread_checks_source_and_bounds_work_after_engine_error():
    image, document = example(("−149087",) * 5)
    original = GenericHistoryAdapter().parse(document)
    engine = RegionEngine(fail=True)
    reread = HistoryAmountReread(Ocr(engine))
    assert reread(image, replace(original, blocked_reason="Неполный снимок.")).blocked_reason
    other_document = replace(document, image_sha256="f" * 64)
    other_history = GenericHistoryAdapter().parse(other_document)
    assert reread(image, other_history) is other_history
    assert not engine.calls
    assert reread(image, original) is original
    assert len(engine.calls) == 1


def test_reread_never_checks_more_than_three_amounts():
    image, document = example(("−149087",) * 5)
    original = GenericHistoryAdapter().parse(document)
    engine = RegionEngine()
    result = HistoryAmountReread(Ocr(engine))(image, original)
    assert len(engine.calls) == 3
    assert [bool(card.blocked_reason) for card in result.cards] == [True, True, True, False, False]


@pytest.mark.parametrize("entry", ["analyze", "history"])
def test_both_history_entry_points_use_the_same_reread(entry, tmp_path):
    image, document = example()
    source = LoadedOcrImage(image.sha256, image)

    class Primary:
        def recognize(self, _image):
            return document

    engine = RegionEngine()
    recognition = CardRecognition(
        Ocr(Primary()),
        lambda _path: source,
        GenericCardAdapter(),
        history_adapter=GenericHistoryAdapter(),
        bank_name=UNIVERSAL_PROFILE,
        history_reread=HistoryAmountReread(Ocr(engine)),
    )
    preview = getattr(recognition, entry)(tmp_path / "synthetic.png")
    assert isinstance(preview, OcrHistoryPreview)
    assert preview.history.cards[0].blocked_reason and len(engine.calls) == 1
