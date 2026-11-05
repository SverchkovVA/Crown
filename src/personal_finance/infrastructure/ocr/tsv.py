"""Строгий ограниченный разбор TSV Tesseract без изменения распознанного текста."""

import re
from decimal import Decimal

from personal_finance.domain.errors import ValidationError
from personal_finance.domain.ocr import (
    MAX_OCR_TSV_BYTES,
    MAX_OCR_WORDS,
    OcrBox,
    OcrDocument,
    OcrEngineInfo,
    OcrImage,
    OcrWord,
)

_HEADER = (
    "level\tpage_num\tblock_num\tpar_num\tline_num\tword_num\tleft\ttop\twidth\theight\tconf\ttext"
)
_ERROR = "Локальный OCR вернул некорректный или слишком большой результат."


def parse_tesseract_tsv(contents: bytes, image: OcrImage, engine: OcrEngineInfo) -> OcrDocument:
    if not isinstance(contents, bytes) or not 1 <= len(contents) <= MAX_OCR_TSV_BYTES:
        raise ValidationError(_ERROR)
    try:
        decoded = contents.decode("utf-8")
    except UnicodeDecodeError:
        raise ValidationError(_ERROR) from None
    rows = decoded.splitlines()
    if not rows or rows[0] != _HEADER or len(rows) > 25_001:
        raise ValidationError(_ERROR)
    words = []
    pages = 0
    for row in rows[1:]:
        columns = row.split("\t")
        if len(columns) != 12 or any(re.fullmatch(r"[0-9]{1,6}", v) is None for v in columns[:10]):
            raise ValidationError(_ERROR)
        level, page, block, paragraph, line, number, left, top, width, height = (
            int(v) for v in columns[:10]
        )
        if (
            not 1 <= level <= 5
            or page != 1
            or re.fullmatch(r"-?[0-9]{1,3}(?:\.[0-9]{1,6})?", columns[10]) is None
            or left + width > image.width
            or top + height > image.height
        ):
            raise ValidationError(_ERROR)
        confidence = Decimal(columns[10])
        if level == 1:
            pages += 1
            if (
                pages != 1
                or (left, top, width, height) != (0, 0, image.width, image.height)
                or (block, paragraph, line, number) != (0, 0, 0, 0)
            ):
                raise ValidationError(_ERROR)
        if pages != 1:
            raise ValidationError(_ERROR)
        if level < 5:
            if columns[11] or confidence != -1:
                raise ValidationError(_ERROR)
            continue
        if not columns[11].strip():
            continue
        try:
            words.append(
                OcrWord(
                    columns[11],
                    OcrBox(left, top, width, height),
                    confidence,
                    (block, paragraph, line),
                    number,
                )
            )
        except ValidationError:
            raise ValidationError(_ERROR) from None
        if len(words) > MAX_OCR_WORDS:
            raise ValidationError(_ERROR)
    if pages != 1:
        raise ValidationError(_ERROR)
    try:
        return OcrDocument(image.sha256, image.width, image.height, engine, tuple(words))
    except ValidationError:
        raise ValidationError(_ERROR) from None
