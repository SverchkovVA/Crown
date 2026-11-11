"""Ограниченное повторное чтение суммы из неизменного изображения истории."""

import re
from dataclasses import dataclass, replace

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
_CURRENCY = r"\s*(?P<currency>RUB|₽|руб\.?|[РP])?"
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


@dataclass(frozen=True)
class _Candidate:
    region: QRect
    amount: QRect
    digits: str
    sign: str
    possible_currency: QRect | None = None


def _same_line(first: QRect, second: QRect) -> bool:
    overlap = min(first.bottom(), second.bottom()) - max(first.top(), second.top()) + 1
    return 2 * overlap >= min(first.height(), second.height())


def _possible_currency(card: OcrCardDraft, amount: QRect, index: int) -> QRect | None:
    doc = card.document
    dates = card.occurred_on.word_indices
    word = doc.words[index]
    if (
        card.currency.value is not None
        or card.occurred_on.value is None
        or not dates
        or set(card.description.word_indices) - set(dates) != {index}
        or card.description.value != word.text
        or not 2 <= len(word.text) <= 4
        or not word.text.isalpha()
        or word.confidence >= 80
    ):
        return None
    date = _envelope(tuple(doc.words[i] for i in dates))
    suffix = _rect(word)
    if (
        date.right() + amount.height() >= amount.left()
        or not _same_line(date, amount)
        or not _same_line(amount, suffix)
        or not 0 <= suffix.left() - amount.right() - 1 < amount.height()
    ):
        return None
    return suffix


def _candidate(card: OcrCardDraft) -> _Candidate | None:
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
    adjacent = [i for i, w in enumerate(doc.words) if i not in refs and region.intersects(_rect(w))]
    possible_currency = None
    included = set(refs)
    if adjacent:
        if len(adjacent) != 1:
            return None
        possible_currency = _possible_currency(card, envelope, adjacent[0])
        if possible_currency is None:
            return None
        included.add(adjacent[0])
        region = envelope.united(possible_currency).adjusted(
            -height, -(height // 2), height, height // 2
        )
    if (
        region.left() <= 0
        or region.top() <= 0
        or region.right() >= doc.width - 1
        or region.bottom() >= doc.height - 1
        or region.width() > 512
        or region.height() > 128
        or (
            possible_currency is not None
            and any(
                region.intersects(_rect(w)) for i, w in enumerate(doc.words) if i not in included
            )
        )
    ):
        return None
    return _Candidate(region, envelope, digits, reading["sign"], possible_currency)


def _matches_position(region: QRect, original: QRect, reread: QRect) -> bool:
    expected = QRect(
        (original.left() - region.left()) * 2,
        (original.top() - region.top()) * 2,
        original.width() * 2,
        original.height() * 2,
    )
    overlap = expected.intersected(reread)
    return 2 * overlap.width() >= reread.width() and 2 * overlap.height() >= reread.height()


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
            attempts += 1
            try:
                conflict = self._differs(pixels, candidate)
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

    def _differs(self, pixels: QImage, candidate: _Candidate) -> bool:
        region = candidate.region
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
        if reading is None or _digits(reading) + reading["fraction"] != candidate.digits:
            return False
        other_sign = reading["sign"]
        if candidate.sign and other_sign and (candidate.sign == "+") != (other_sign == "+"):
            return False
        if candidate.possible_currency is None:
            return _matches_position(region, candidate.amount, _envelope(reread.words))

        # Отдельное слово валюты должно перекрыть исходное короткое слово.
        currency = reading["currency"]
        if currency is None or currency.casefold() not in {"rub", "₽", "руб", "руб."}:
            return False
        row = rows[0]
        money_words = row.words(reread, 0, reading.end("fraction"))
        currency_words = row.words(reread, *reading.span("currency"))
        if not money_words or len(currency_words) != 1 or currency_words[0] in money_words:
            return False
        return _matches_position(
            region, candidate.amount, _envelope(tuple(reread.words[i] for i in money_words))
        ) and _matches_position(
            region, candidate.possible_currency, _rect(reread.words[currency_words[0]])
        )
