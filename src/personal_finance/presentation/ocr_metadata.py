"""Подсказки OCR и точное совпадение с пользовательской категорией."""

from personal_finance.domain.bonuses import format_units
from personal_finance.domain.ledger import OperationKind
from personal_finance.domain.models import Category, CategoryKind
from personal_finance.domain.ocr_metadata import OcrOperationContext


def context_note(value: OcrOperationContext) -> str:
    parts = []
    if value.bank_category:
        parts.append("Категория в банке: " + value.bank_category)
    if value.bonuses:
        parts.append(
            "Бонусные подписи: "
            + ", ".join(
                ("+" if b.units_minor > 0 else "") + format_units(b.units_minor)
                for b in value.bonuses
            )
            + ". Это не денежный доход."
        )
    return "\n".join(parts)


def bank_category_match(
    value: OcrOperationContext, categories: list[Category], kind: OperationKind
) -> str | None:
    required = (
        CategoryKind.EXPENSE
        if kind is OperationKind.EXPENSE
        else CategoryKind.INCOME
        if kind is OperationKind.INCOME
        else None
    )
    normalized = " ".join(value.bank_category.casefold().split())
    if not normalized or required is None:
        return None
    found = [
        c.id
        for c in categories
        if c.kind is required and " ".join(c.name.casefold().split()) == normalized
    ]
    return found[0] if len(found) == 1 else None
