"""Распознавание без открытой книги, записи фактов и доступа к сети."""

from typing import Protocol

from personal_finance.domain.errors import ValidationError
from personal_finance.domain.ocr import OcrDocument, OcrImage


class OcrEngine(Protocol):
    def recognize(self, image: OcrImage) -> OcrDocument: ...


class Ocr:
    def __init__(self, engine: OcrEngine) -> None:
        self._engine = engine

    def recognize(self, image: OcrImage) -> OcrDocument:
        if not isinstance(image, OcrImage):
            raise ValidationError("Выберите изображение для распознавания.")
        result = self._engine.recognize(image)
        if (
            not isinstance(result, OcrDocument)
            or result.image_sha256 != image.sha256
            or (result.width, result.height) != (image.width, image.height)
        ):
            raise ValidationError("Ответ OCR относится к другому изображению.")
        return result
