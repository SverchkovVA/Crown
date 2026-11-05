"""Извлечение подписей без изменения денежного аудита и исходных слов."""

import hashlib
import re
from dataclasses import replace

from personal_finance.domain.errors import ValidationError
from personal_finance.domain.models import Money
from personal_finance.domain.ocr_cards import OcrCardDraft
from personal_finance.domain.ocr_metadata import OcrBonusSuggestion, OcrOperationContext
from personal_finance.infrastructure.ocr.generic import (
    _CURRENCY,
    _DETAILS,
    _HEADING,
    _HISTORY,
    _NUMBER,
    _SUMMARY,
    Row,
    _dates,
    _money,
    card_reward_rows,
    history_subtitles,
    visual_rows,
)


def _category(card: OcrCardDraft, row: Row) -> tuple[str, tuple[int, ...]]:
    document = card.document
    amounts = _money(document, row)
    refs = list(row.refs)
    if amounts:
        first = amounts[0][2]
        refs = list(row.words(document, 0, first))
    if not refs:
        return "", ()
    selected: list[int] = []
    for ref in refs:
        word = document.words[ref]
        if selected:
            previous = document.words[selected[-1]].box
            if word.box.left - previous.left - previous.width > 2 * row.height:
                break
        selected.append(ref)
    text = " ".join(document.words[i].text for i in selected).strip(" ·:—−-=><#?")
    if not text or len(text) > 120 or not re.search("[А-Яа-яЁёA-Za-z]", text):
        return "", ()
    if (
        _DETAILS.match(text)
        or _SUMMARY.match(text)
        or _HEADING.fullmatch(text)
        or _HISTORY.fullmatch(text)
    ):
        return "", ()
    return text, tuple(selected)


def operation_context(card: OcrCardDraft) -> OcrOperationContext:
    doc = card.document
    rows = visual_rows(doc)
    anchor = next(
        (i for i, row in enumerate(rows) if set(card.amount.word_indices).intersection(row.refs)),
        None,
    )
    if anchor is None:
        return OcrOperationContext()
    subtitle_map = history_subtitles(doc, rows)
    explicit = card_reward_rows(doc, rows)
    annotation_indices = sorted(set(subtitle_map) | explicit)
    relevant = [
        i
        for i in annotation_indices
        if subtitle_map.get(i) == anchor or i in explicit and rows[i].top > rows[anchor].bottom
    ]
    bonuses = []
    for index in relevant:
        amounts = _money(doc, rows[index])
        if len(amounts) != 1:
            continue
        for token, refs, _, _ in amounts:
            match = re.fullmatch(
                rf"(?P<sign>[+\-−—–])\s*(?P<number>{_NUMBER})\s*(?P<unit>{_CURRENCY})?", token, re.I
            )
            if match is None:
                continue
            try:
                units = Money.parse(match["number"]).minor
            except ValidationError:
                continue
            if not units:
                continue
            if match["sign"] != "+":
                units = -units
            source = hashlib.sha256(
                (doc.image_sha256 + ":bonus:" + str(annotation_indices.index(index))).encode()
            ).hexdigest()
            bonuses.append(OcrBonusSuggestion(units, refs, source, match["unit"] or ""))
    category = ""
    category_refs: tuple[int, ...] = ()
    if anchor + 1 < len(rows):
        following = rows[anchor + 1]
        main = rows[anchor]
        amount_height = max(
            (doc.words[i].box.height for i in card.amount.word_indices), default=main.height
        )
        if (
            0
            <= following.top
            - max(doc.words[i].box.top + doc.words[i].box.height for i in card.amount.word_indices)
            <= 2 * amount_height
            and following.height < amount_height
            and not _dates(doc, following)
            and (not _money(doc, following) or anchor + 1 in subtitle_map)
        ):
            category, category_refs = _category(card, following)
    if not category and anchor >= 2:
        candidate, previous = rows[anchor - 1], rows[anchor - 2]
        if (
            0 <= rows[anchor].top - candidate.bottom <= 3 * rows[anchor].height
            and 0 <= candidate.top - previous.bottom <= 2 * max(previous.height, candidate.height)
            and candidate.height < rows[anchor].height
            and not _dates(doc, candidate)
            and not _money(doc, candidate)
            and not _dates(doc, previous)
            and not _money(doc, previous)
        ):
            category, category_refs = _category(card, candidate)
    return OcrOperationContext(category, category_refs, tuple(bonuses))


def with_operation_context(card: OcrCardDraft) -> OcrCardDraft:
    return replace(card, metadata=operation_context(card))
