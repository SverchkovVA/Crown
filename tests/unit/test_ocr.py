"""Только вымышленные PNG/TSV; пользовательские изображения не читаются."""

import hashlib
import struct
from dataclasses import FrozenInstanceError, replace
from decimal import Decimal

import pytest

from personal_finance.application.ocr import Ocr
from personal_finance.domain.errors import ValidationError
from personal_finance.domain.ocr import (
    MAX_OCR_IMAGE_BYTES,
    MAX_OCR_TSV_BYTES,
    OcrBox,
    OcrEngineInfo,
    OcrImage,
    OcrWord,
)
from personal_finance.infrastructure.ocr.tsv import parse_tesseract_tsv

ENGINE = OcrEngineInfo("tesseract", "5.5.3.20260724", (("rus", "1" * 64), ("eng", "2" * 64)))
HEADER = (
    "level\tpage_num\tblock_num\tpar_num\tline_num\tword_num\t"
    "left\ttop\twidth\theight\tconf\ttext\n"
)
PAGE = "1\t1\t0\t0\t0\t0\t0\t0\t400\t300\t-1\t\n"
WORD = "5\t1\t1\t1\t1\t1\t10\t20\t80\t30\t96.123456\t-1 234,56\n"


def image():
    # Предметный DTO проверяет заголовок; реальная декодировка проверена отдельно.
    return OcrImage(
        b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR" + struct.pack(">II", 400, 300) + b"\0" * 9, 400, 300
    )


def parse(text):
    return parse_tesseract_tsv(text.encode(), image(), ENGINE)


def test_result_keeps_exact_text_confidence_box_and_fingerprints():
    result = parse(HEADER + PAGE + WORD)
    word = result.words[0]
    assert word.text == "-1 234,56"
    assert word.confidence == Decimal("96.123456")
    assert word.box == OcrBox(10, 20, 80, 30)
    assert result.image_sha256 == hashlib.sha256(image().png).hexdigest()
    assert result.engine == ENGINE
    assert "-1 234,56" not in repr(result) + repr(word)
    assert "\\x89PNG" not in repr(image())
    with pytest.raises(FrozenInstanceError):
        result.width = 1


def test_line_order_is_stable_and_words_use_engine_number_without_rewriting():
    last = WORD.replace("\t1\t10\t", "\t3\t10\t").replace("-1 234,56", "О0")
    second = WORD.replace("\t1\t10\t", "\t2\t10\t").replace("-1 234,56", "Слово")
    next_line = WORD.replace("\t1\t1\t10\t", "\t2\t1\t10\t")
    result = parse(HEADER + PAGE + last + second + WORD + next_line)
    assert [[w.text for w in line] for line in result.lines()] == [
        ["-1 234,56", "Слово", "О0"],
        ["-1 234,56"],
    ]


@pytest.mark.parametrize(
    "value",
    [
        "",
        HEADER,
        HEADER + WORD,
        HEADER + PAGE + PAGE,
        HEADER.replace("conf", "confidence") + PAGE,
        HEADER + PAGE.replace("400", "401"),
        HEADER + PAGE.replace("\t1\t0\t", "\t2\t0\t"),
        HEADER + PAGE + WORD.replace("96.123456", "NaN"),
        HEADER + PAGE + WORD.replace("96.123456", "Infinity"),
        HEADER + PAGE + WORD.replace("96.123456", "100.000001"),
        HEADER + PAGE + WORD.replace("96.123456", "-0.1"),
        HEADER + PAGE + WORD.replace("\t80\t30\t", "\t0\t30\t"),
        HEADER + PAGE + WORD.replace("\t10\t20\t", "\t390\t20\t"),
        HEADER + PAGE + WORD.replace("\t1\t1\t1\t1\t10\t", "\t0\t1\t1\t1\t10\t"),
        HEADER + PAGE + WORD + WORD,
        HEADER + PAGE + WORD.replace("-1 234,56", "личное\tполе"),
        HEADER + PAGE + WORD.replace("-1 234,56", "\x00секрет"),
        HEADER + PAGE.replace("-1\t", "0\t"),
        HEADER + PAGE.replace("-1\t\n", "-1\tличное\n"),
        HEADER + PAGE + WORD.replace("5\t1", "6\t1"),
    ],
)
def test_malformed_or_ambiguous_tsv_is_rejected_without_echo(value):
    with pytest.raises(ValidationError) as caught:
        parse(value)
    assert "личное" not in str(caught.value) and "секрет" not in str(caught.value)


def test_utf8_and_result_size_are_checked_before_parsing():
    for value in (b"\xff", b"x" * (MAX_OCR_TSV_BYTES + 1)):
        with pytest.raises(ValidationError):
            parse_tesseract_tsv(value, image(), ENGINE)


def test_empty_recognition_is_valid_and_is_not_a_financial_fact():
    result = parse(HEADER + PAGE)
    assert result.words == () and result.lines() == ()
    assert parse(HEADER + PAGE + WORD.replace("-1 234,56", "  ")).words == ()


@pytest.mark.parametrize(
    "changes",
    [
        {"width": True},
        {"height": 0},
        {"width": 10_001},
        {"width": 10_000, "height": 10_000},
        {"png": b"not PNG"},
        {"png": image().png + b"0" * MAX_OCR_IMAGE_BYTES},
        {"width": 399},
    ],
)
def test_input_limits_and_png_geometry(changes):
    with pytest.raises(ValidationError):
        replace(image(), **changes)


@pytest.mark.parametrize(
    "confidence", [float("nan"), Decimal("NaN"), Decimal("Infinity"), Decimal(-1)]
)
def test_word_confidence_is_finite_decimal(confidence):
    with pytest.raises(ValidationError):
        OcrWord("текст", OcrBox(1, 2, 3, 4), confidence, (1, 1, 1), 1)


@pytest.mark.parametrize(
    "changes",
    [
        {"image_sha256": "?" * 64},
        {"words": []},
        {"words": (OcrWord("текст", OcrBox(399, 299, 2, 2), Decimal(90), (1, 1, 1), 1),)},
    ],
)
def test_document_rejects_invalid_provenance_and_geometry(changes):
    with pytest.raises(ValidationError):
        replace(parse(HEADER + PAGE), **changes)


def test_engine_model_identity_cannot_be_missing_or_duplicate():
    for models in ((), (("rus", "bad"),), (("rus", "1" * 64), ("rus", "2" * 64))):
        with pytest.raises(ValidationError):
            OcrEngineInfo("tesseract", "5", models)


def test_application_passes_immutable_image_and_rejects_mismatched_result():
    class Engine:
        def recognize(self, value):
            assert value == image()
            return parse(HEADER + PAGE + WORD)

    result = Ocr(Engine()).recognize(image())
    assert result.words[0].text == "-1 234,56"

    class Wrong:
        def recognize(self, value):
            return replace(result, image_sha256="f" * 64)

    with pytest.raises(ValidationError, match="другому"):
        Ocr(Wrong()).recognize(image())


def test_tsv_limits_word_count_and_row_count():
    rows = [WORD.replace("\t1\t10\t", f"\t{number}\t10\t") for number in range(1, 10_002)]
    with pytest.raises(ValidationError):
        parse(HEADER + PAGE + "".join(rows))
    with pytest.raises(ValidationError):
        parse(HEADER + PAGE + ("\n" * 25_000))


def test_nonbreaking_spaces_in_recognized_amount_are_preserved_without_correction():
    raw = "-1\u202f234,56\u00a0RUB"
    result = parse(HEADER + PAGE + WORD.replace("-1 234,56", raw))
    assert result.words[0].text == raw
