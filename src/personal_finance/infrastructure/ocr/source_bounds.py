"""Общая проверка полноты области исходной суммы, без изменения полей OCR."""

from dataclasses import replace

from personal_finance.domain.ocr_cards import OcrCardDraft


def block_clipped_amount(card: OcrCardDraft) -> OcrCardDraft:
    """Сохранить поля для сверки, но не принимать сумму в 8 пикселях от края."""
    if card.blocked_reason:
        return card
    document = card.document
    for index in card.amount.word_indices:
        box = document.words[index].box
        if (
            box.left <= 8
            or box.top <= 8
            or box.left + box.width >= document.width - 8
            or box.top + box.height >= document.height - 8
        ):
            return replace(
                card,
                blocked_reason="Сумма находится у края изображения. Нужен полный снимок.",
            )
    return card
