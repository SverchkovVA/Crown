"""Вымышленные карточки; не копируют пользовательские изображения."""

import hashlib
import struct
from decimal import Decimal

from personal_finance.domain.ocr import OcrBox, OcrDocument, OcrEngineInfo, OcrImage, OcrWord
from personal_finance.infrastructure.ocr.t_bank import TBankCardAdapter

ENGINE = OcrEngineInfo("tesseract", "synthetic", (("rus", "1" * 64), ("eng", "2" * 64)))
LINES = (
    "Т-Банк",
    "Перевод",
    "-1 234,56 RUB",
    "27 октября 2026 в 14:35",
    "Описание: Вымышленный пример",
)


def image():
    return OcrImage(
        b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR" + struct.pack(">II", 1080, 1400) + b"\0" * 9,
        1080,
        1400,
    )


def document(lines=LINES, *, image_sha256=None, confidence=Decimal(96)):
    return OcrDocument(
        image_sha256 or hashlib.sha256(image().png).hexdigest(),
        1080,
        1400,
        ENGINE,
        tuple(
            OcrWord(text, OcrBox(50, 80 + i * 80, 900, 50), confidence, (1, 1, i + 1), 1)
            for i, text in enumerate(lines)
        ),
    )


def card(lines=LINES, **kwargs):
    return TBankCardAdapter().parse(document(lines, **kwargs))


def png_image():
    from PySide6.QtCore import QBuffer, QIODevice
    from PySide6.QtGui import QImage, QImageWriter

    pixels = QImage(1080, 1400, QImage.Format.Format_RGB32)
    pixels.fill("white")
    buffer = QBuffer()
    buffer.open(QIODevice.OpenModeFlag.WriteOnly)
    assert QImageWriter(buffer, b"PNG").write(pixels)
    return OcrImage(buffer.data().data(), 1080, 1400)


def history_document(
    *,
    header="27 октября 2026",
    values=("-1 234,56 RUB", "+50,00 Р"),
    amount_left=650,
    image_sha256=None,
    bottom=False,
):
    lines = [(50, [(header, 50)])]
    for index, value in enumerate(values):
        top = 1360 if bottom else 150 + index * 120
        pieces = [
            ("Магазин", 50),
            *[(v, amount_left + i * 100) for i, v in enumerate(value.split())],
        ]
        lines.append((top, pieces))
    words = []
    for line, (top, tokens) in enumerate(lines, 1):
        for number, (text, left) in enumerate(tokens, 1):
            words.append(
                OcrWord(text, OcrBox(left, top, 80, 35), Decimal(95), (1, 1, line), number)
            )
    return OcrDocument(image_sha256 or image().sha256, 1080, 1400, ENGINE, tuple(words))


VTB_CARD = ("Детали операции", "-1 234,56 RUB", "Дата и время", "27.10.2026 14:35")


def vtb_history_document(
    *,
    header="27 октября 2026",
    values=("-1 234,56 RUB", "+50,00 Р"),
    summary=True,
    heading=True,
    amount_left=700,
    bottom=False,
):
    lines = []
    if heading:
        lines.append((5, [("История операций", 20, 400)]))
    date_tokens = [(header, 20, 220)]
    if summary:
        date_tokens.append(("-9 876,54 RUB", 280, 250))
    lines.append((50, date_tokens))
    for index, text in enumerate(values):
        top = 1390 if bottom else 100 + index * 12
        lines.append(
            (
                top,
                [
                    ("Вымышленный магазин", 20, 300),
                    (text, amount_left, min(350, 1070 - amount_left)),
                ],
            )
        )
    words = tuple(
        OcrWord(text, OcrBox(left, top, width, 10), Decimal(95), (1, 1, number), word)
        for number, (top, tokens) in enumerate(lines, 1)
        for word, (text, left, width) in enumerate(tokens, 1)
    )
    return OcrDocument(image().sha256, 1080, 1400, ENGINE, words)
