"""Геометрия исходной области суммы; слова и координаты OCR не изменяются."""

from dataclasses import replace
from itertools import pairwise

from personal_finance.domain.ocr import OcrBox, OcrWord
from personal_finance.domain.ocr_cards import OcrCardDraft, OcrField, OcrIssue


def block_clipped_amount(card: OcrCardDraft) -> OcrCardDraft:
    """Не принимать сумму, если связанная с ней строка подходит к краю."""
    if card.blocked_reason or not card.amount.word_indices:
        return card
    document = card.document
    amount_indices = set(card.amount.word_indices)

    def at_edge(box: OcrBox) -> bool:
        return (
            box.left <= 8
            or box.top <= 8
            or box.left + box.width >= document.width - 8
            or box.top + box.height >= document.height - 8
        )

    # Валюта у края может стать неизвестным словом и не попасть в поле суммы.
    # Связь задаёт геометрия, независимо от разбиения движком на строки/слова.
    bands = {(document.words[i].box.top, document.words[i].box.height) for i in amount_indices}
    for top, height in bands:
        nearby = sorted(
            (
                (index, word.box)
                for index, word in enumerate(document.words)
                if 2 * (min(top + height, word.box.top + word.box.height) - max(top, word.box.top))
                >= min(height, word.box.height)
            ),
            key=lambda item: item[1].left,
        )
        right = row_height = 0
        has_amount = clipped = False
        for index, box in nearby:
            if box.left - right > 2 * max(row_height, box.height):
                has_amount = clipped = False
                right = row_height = 0
            has_amount = has_amount or index in amount_indices
            clipped = clipped or at_edge(box)
            right = max(right, box.left + box.width)
            row_height = max(row_height, box.height)
            if has_amount and clipped:
                return replace(
                    card,
                    blocked_reason="Строка суммы у края изображения. Нужен полный снимок.",
                )
    return card


def block_disconnected_amount(card: OcrCardDraft) -> OcrCardDraft:
    """Не соединять числовые разряды из раздельных областей строки."""
    if card.amount.value is None:
        return card
    words = tuple(card.document.words[i] for i in card.amount.word_indices)
    numeric = sorted(
        (word for word in words if any("0" <= char <= "9" for char in word.text)),
        key=lambda word: (2 * word.box.top + word.box.height, word.box.left),
    )
    punctuation = tuple(word for word in words if word.text.strip() in (",", "."))
    rows: list[list[OcrWord]] = []
    for word in numeric:
        box = word.box
        for row in reversed(rows[-3:]):
            anchor = row[0].box
            overlap = min(anchor.top + anchor.height, box.top + box.height) - max(
                anchor.top, box.top
            )
            if 2 * overlap >= min(anchor.height, box.height):
                row.append(word)
                break
        else:
            rows.append([word])

    def decimal_between(previous: OcrWord, current: OcrWord) -> bool:
        if previous.text.rstrip().endswith((",", ".")) or current.text.lstrip().startswith(
            (",", ".")
        ):
            return True
        left, right = previous.box, current.box
        return any(
            left.left + left.width <= mark.box.left
            and mark.box.left + mark.box.width <= right.left
            and (
                mark.line_key in (previous.line_key, current.line_key)
                or 2
                * (
                    min(left.top + left.height, mark.box.top + mark.box.height)
                    - max(left.top, mark.box.top)
                )
                >= min(left.height, mark.box.height)
            )
            for mark in punctuation
        )

    # Равные повторные отображения суммы на разных строках не соединяются.
    for row in rows:
        for previous, current in pairwise(sorted(row, key=lambda word: word.box.left)):
            left, right = previous.box, current.box
            gap = right.left - left.left - left.width
            if gap > 2 * max(left.height, right.height) and not decimal_between(previous, current):
                return replace(
                    card,
                    amount=OcrField(
                        None,
                        card.amount.word_indices,
                        tuple(dict.fromkeys((*card.amount.issues, OcrIssue.AMBIGUOUS))),
                    ),
                    blocked_reason=card.blocked_reason
                    or "Части суммы находятся в разных колонках. Нужен более чёткий снимок.",
                )
    return card
