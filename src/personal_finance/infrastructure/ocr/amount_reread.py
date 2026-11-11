"""Ограниченное повторное чтение суммы из неизменного изображения истории."""

import re
from dataclasses import replace

from PySide6.QtCore import QRect, Qt
from PySide6.QtGui import QImage

from personal_finance.application.ocr import Ocr
from personal_finance.domain.errors import FinanceError
from personal_finance.domain.ocr import OcrImage, OcrWord
from personal_finance.domain.ocr_cards import OcrCardDraft, OcrHistoryDraft, OcrIssue
from personal_finance.infrastructure.ocr.generic import visual_rows
from personal_finance.infrastructure.ocr.images import normalize_ocr_image

MAX_AMOUNT_REREADS = 3
AMOUNT_REREAD_TIMEOUT_SECONDS = 2
_AMOUNT_CONFLICT = "Сумма прочитана по-разному. Нужен более чёткий снимок."
_SPACES = " \u00a0\u202f"
_INTEGER = r"(?:[0-9]+|[0-9]{1,3}(?:[ \u00a0\u202f][0-9]{3})+)"
_SIGN = r"(?P<sign>[+\-−—–]?)\s*"
_CURRENCY = r"\s*(?:RUB|₽|руб\.?|[РP])?"
_WHOLE_AMOUNT = re.compile(_SIGN + rf"(?P<whole>{_INTEGER})" + _CURRENCY, re.I)
_FRACTIONAL_AMOUNT = re.compile(
    _SIGN + rf"(?P<whole>{_INTEGER})[,.](?P<fraction>[0-9]{{1,2}})" + _CURRENCY, re.I
)


def _digits(match: re.Match[str]) -> str:
    return match["whole"].translate(str.maketrans("", "", _SPACES))


def _rect(word: OcrWord) -> QRect:
    box = word.box
    return QRect(box.left, box.top, box.width, box.height)


def _envelope(words: tuple[OcrWord, ...]) -> QRect:
    result = QRect()
    for word in words:
        result = result.united(_rect(word))
    return result


def _candidate(card: OcrCardDraft) -> tuple[QRect, QRect, str, str] | None:
    refs = card.amount.word_indices
    if (
        card.blocked_reason
        or card.amount.value is None
        or not refs
        or card.currency.value not in (None, "RUB")
        or OcrIssue.LOW_CONFIDENCE not in card.amount.issues
    ):
        return None
    doc = card.document
    words = tuple(sorted((doc.words[i] for i in refs), key=lambda w: w.box.left))
    reading = _WHOLE_AMOUNT.fullmatch(" ".join(w.text for w in words))
    if reading is None:
        return None
    digits = _digits(reading)
    if len(digits) > 18 or int(digits) * 100 != card.amount.value:
        return None
    envelope = _envelope(words)
    height = max(w.box.height for w in words)
    # Слова из разных строк нельзя объединять в новую область чтения.
    overlap = min(w.box.top + w.box.height for w in words) - max(w.box.top for w in words)
    if 2 * overlap < min(w.box.height for w in words):
        return None
    region = envelope.adjusted(-height, -(height // 2), height, height // 2)
    if (
        region.left() <= 0
        or region.top() <= 0
        or region.right() >= doc.width - 1
        or region.bottom() >= doc.height - 1
        or region.width() > 512
        or region.height() > 128
        or any(region.intersects(_rect(w)) for i, w in enumerate(doc.words) if i not in refs)
    ):
        return None
    return region, envelope, digits, reading["sign"]


class HistoryAmountReread:
    """Второй проход сообщает о конфликте; ничего не исправляет и не сохраняет."""

    def __init__(self, ocr: Ocr) -> None:
        self._ocr = ocr

    def __call__(self, image: OcrImage, history: OcrHistoryDraft) -> OcrHistoryDraft:
        document = history.document
        if (
            history.blocked_reason
            or document.image_sha256 != image.sha256
            or (document.width, document.height) != (image.width, image.height)
        ):
            return history

        pixels: QImage | None = None
        cards = list(history.cards)
        attempts = 0
        changed = False
        for index, card in enumerate(cards):
            if attempts >= MAX_AMOUNT_REREADS:
                break
            candidate = _candidate(card)
            if candidate is None:
                continue
            if pixels is None:
                pixels = QImage.fromData(image.png)
                if pixels.isNull() or (pixels.width(), pixels.height()) != (
                    image.width,
                    image.height,
                ):
                    return history
            region, envelope, digits, sign = candidate
            attempts += 1
            try:
                conflict = self._differs(pixels, region, envelope, digits, sign)
            except FinanceError:
                # Дополнительная попытка не отменяет первый результат. После
                # ошибки не продолжаем повторные вызовы на остальных строках.
                break
            if conflict:
                cards[index] = replace(
                    card,
                    amount=replace(
                        card.amount, value=None, issues=(*card.amount.issues, OcrIssue.AMBIGUOUS)
                    ),
                    blocked_reason=_AMOUNT_CONFLICT,
                )
                changed = True
        return replace(history, cards=tuple(cards)) if changed else history

    def _differs(
        self, pixels: QImage, region: QRect, envelope: QRect, digits: str, sign: str
    ) -> bool:
        crop = pixels.copy(region).scaled(
            region.width() * 2,
            region.height() * 2,
            Qt.AspectRatioMode.IgnoreAspectRatio,
            Qt.TransformationMode.SmoothTransformation,
        )
        reread = self._ocr.recognize(normalize_ocr_image(crop))
        rows = visual_rows(reread)
        if len(rows) != 1:
            return False
        reading = _FRACTIONAL_AMOUNT.fullmatch(rows[0].text)
        if reading is None or _digits(reading) + reading["fraction"] != digits:
            return False
        other_sign = reading["sign"]
        if sign and other_sign and (sign == "+") != (other_sign == "+"):
            return False

        expected = QRect(
            (envelope.left() - region.left()) * 2,
            (envelope.top() - region.top()) * 2,
            envelope.width() * 2,
            envelope.height() * 2,
        )
        seen = _envelope(reread.words)
        overlap = expected.intersected(seen)
        return 2 * overlap.width() >= seen.width() and 2 * overlap.height() >= seen.height()
