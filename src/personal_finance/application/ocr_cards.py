"""Подготовка карточки без сессии книги и доступа к финансовому журналу."""

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from personal_finance.application.ocr import Ocr
from personal_finance.domain.errors import ValidationError
from personal_finance.domain.ocr import LoadedOcrImage, OcrDocument
from personal_finance.domain.ocr_cards import OcrCardDraft, OcrHistoryDraft


def require_bank_name(value: str) -> None:
    if value not in ("Т-Банк", "ВТБ"):
        raise ValidationError("Неизвестный профиль OCR-банка.")


class CardAdapter(Protocol):
    def parse(self, document: OcrDocument) -> OcrCardDraft: ...


@dataclass(frozen=True, slots=True)
class OcrCardPreview:
    source: LoadedOcrImage
    card: OcrCardDraft
    bank_name: str = "Т-Банк"

    def __post_init__(self) -> None:
        require_bank_name(self.bank_name)
        if self.card.document.image_sha256 != self.source.image.sha256 or (
            self.card.document.width,
            self.card.document.height,
        ) != (self.source.image.width, self.source.image.height):
            raise ValidationError("Карточка относится к другому изображению.")


class HistoryAdapter(Protocol):
    def parse(self, document: OcrDocument) -> OcrHistoryDraft: ...


@dataclass(frozen=True, slots=True)
class OcrHistoryPreview:
    source: LoadedOcrImage
    history: OcrHistoryDraft
    bank_name: str = "Т-Банк"

    def __post_init__(self) -> None:
        require_bank_name(self.bank_name)
        if self.source.image.sha256 != self.history.document.image_sha256 or (
            self.source.image.width,
            self.source.image.height,
        ) != (self.history.document.width, self.history.document.height):
            raise ValidationError("История относится к другому изображению.")


class CardRecognition:
    def __init__(
        self,
        ocr: Ocr,
        loader: Callable[[Path], LoadedOcrImage],
        adapter: CardAdapter,
        *,
        history_adapter: HistoryAdapter | None = None,
        bank_name: str = "Т-Банк",
    ) -> None:
        require_bank_name(bank_name)
        self.bank_name = bank_name
        self._ocr, self._loader, self._adapter = ocr, loader, adapter
        self._history_adapter = history_adapter

    def preview(self, path: Path) -> OcrCardPreview:
        source = self._loader(path)
        document = self._ocr.recognize(source.image)
        card = self._adapter.parse(document)
        if card.document != document:
            raise ValidationError("Адаптер вернул поля другого OCR-документа.")
        return OcrCardPreview(source, card, self.bank_name)

    @property
    def supports_history(self) -> bool:
        return self._history_adapter is not None

    def history(self, path: Path) -> OcrHistoryPreview:
        if self._history_adapter is None:
            raise ValidationError("Адаптер истории ещё не подключён.")
        source = self._loader(path)
        document = self._ocr.recognize(source.image)
        history = self._history_adapter.parse(document)
        if history.document != document:
            raise ValidationError("Адаптер вернул другую историю.")
        return OcrHistoryPreview(source, history, self.bank_name)
