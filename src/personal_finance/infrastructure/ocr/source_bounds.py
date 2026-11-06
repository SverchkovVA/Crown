"""Геометрия исходной области суммы; слова и координаты OCR не изменяются."""

from dataclasses import replace
from itertools import pairwise

from personal_finance.domain.ocr import OcrBox
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
    numeric = sorted(
        (
            card.document.words[i].box
            for i in card.amount.word_indices
            if any("0" <= char <= "9" for char in card.document.words[i].text)
        ),
        key=lambda box: (2 * box.top + box.height, box.left),
    )
    rows: list[list[OcrBox]] = []
    for box in numeric:
        for row in reversed(rows[-3:]):
            anchor = row[0]
            overlap = min(anchor.top + anchor.height, box.top + box.height) - max(
                anchor.top, box.top
            )
            if 2 * overlap >= min(anchor.height, box.height):
                row.append(box)
                break
        else:
            rows.append([box])
    # Равные повторные отображения суммы на разных строках не соединяются.
    for row in rows:
        for previous, current in pairwise(sorted(row, key=lambda box: box.left)):
            gap = current.left - previous.left - previous.width
            if gap > 2 * max(previous.height, current.height):
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
