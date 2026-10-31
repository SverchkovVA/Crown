"""Поля карточки и ручная проверка; финансовые факты здесь не создаются."""

import re
from dataclasses import dataclass, field
from datetime import date, datetime, time
from decimal import Decimal
from enum import StrEnum

from personal_finance.domain.errors import ValidationError
from personal_finance.domain.models import MAX_MINOR, Money
from personal_finance.domain.ocr import OcrDocument
from personal_finance.domain.ocr_metadata import OcrOperationContext


class OcrIssue(StrEnum):
    MISSING = "missing"
    INVALID = "invalid"
    AMBIGUOUS = "ambiguous"
    LOW_CONFIDENCE = "low_confidence"
    UNRECOGNIZED = "unrecognized"


ISSUE_LABELS = {
    OcrIssue.MISSING: "Не найдено",
    OcrIssue.INVALID: "Проверьте формат",
    OcrIssue.AMBIGUOUS: "Несколько вариантов",
    OcrIssue.LOW_CONFIDENCE: "Низкая оценка слов",
    OcrIssue.UNRECOGNIZED: "Не распознано однозначно",
}


class CashDirection(StrEnum):
    IN = "in"
    OUT = "out"


DIRECTION_LABELS = {CashDirection.IN: "Поступление", CashDirection.OUT: "Списание"}


@dataclass(frozen=True, slots=True)
class OcrField[T]:
    value: T | None = field(repr=False)
    word_indices: tuple[int, ...] = ()
    issues: tuple[OcrIssue, ...] = ()


@dataclass(frozen=True, slots=True)
class OcrCardDraft:
    document: OcrDocument = field(repr=False)
    adapter: str
    amount: OcrField[int]
    currency: OcrField[str]
    direction: OcrField[CashDirection]
    occurred_on: OcrField[date]
    local_time: OcrField[time]
    description: OcrField[str]
    blocked_reason: str = ""
    metadata: OcrOperationContext = field(
        default_factory=OcrOperationContext, repr=False, compare=False
    )

    def __post_init__(self) -> None:
        if (
            not isinstance(self.document, OcrDocument)
            or not isinstance(self.adapter, str)
            or re.fullmatch(r"[a-z][a-z0-9-]{2,79}", self.adapter) is None
        ):
            raise ValidationError("Некорректный результат разбора карточки.")
        if not isinstance(self.metadata, OcrOperationContext):
            raise ValidationError("Некорректные подсказки OCR.")
        extra_refs = self.metadata.category_indices + tuple(
            i for bonus in self.metadata.bonuses for i in bonus.word_indices
        )
        if any(type(i) is not int or not 0 <= i < len(self.document.words) for i in extra_refs):
            raise ValidationError("Подсказка OCR относится к другому изображению.")
        for value in self.fields():
            if (
                not isinstance(value, OcrField)
                or not isinstance(value.word_indices, tuple)
                or len(set(value.word_indices)) != len(value.word_indices)
                or any(
                    type(i) is not int or not 0 <= i < len(self.document.words)
                    for i in value.word_indices
                )
                or not isinstance(value.issues, tuple)
                or any(not isinstance(issue, OcrIssue) for issue in value.issues)
            ):
                raise ValidationError("Область поля не относится к распознанному изображению.")

        if (
            (
                self.amount.value is not None
                and (type(self.amount.value) is not int or not 1 <= self.amount.value <= MAX_MINOR)
            )
            or (
                self.currency.value is not None
                and (
                    not isinstance(self.currency.value, str)
                    or re.fullmatch(r"[A-Z]{3}", self.currency.value) is None
                )
            )
            or (
                self.direction.value is not None
                and not isinstance(self.direction.value, CashDirection)
            )
            or (self.occurred_on.value is not None and type(self.occurred_on.value) is not date)
            or (self.local_time.value is not None and type(self.local_time.value) is not time)
            or (
                self.description.value is not None
                and (
                    not isinstance(self.description.value, str) or len(self.description.value) > 500
                )
            )
        ):
            raise ValidationError("Некорректное значение поля карточки.")

    def fields(
        self,
    ) -> tuple[
        OcrField[int] | OcrField[str] | OcrField[CashDirection] | OcrField[date] | OcrField[time],
        ...,
    ]:
        return (
            self.amount,
            self.currency,
            self.direction,
            self.occurred_on,
            self.local_time,
            self.description,
        )

    def confidence[T](self, value: OcrField[T]) -> Decimal | None:
        if not value.word_indices:
            return None
        return min(self.document.words[i].confidence for i in value.word_indices)


@dataclass(frozen=True, slots=True)
class CardCorrections:
    amount: str = field(repr=False)
    currency: str
    direction: CashDirection | None
    occurred_on: str = field(repr=False)
    local_time: str = field(default="", repr=False)
    description: str = field(default="", repr=False)


@dataclass(frozen=True, slots=True)
class ReviewedOcrCard:
    """Результат проверки в памяти; не OperationDraft и не команда записи."""

    source: OcrCardDraft = field(repr=False)
    amount: Money = field(repr=False)
    direction: CashDirection
    occurred_on: date = field(repr=False)
    local_time: time | None = field(repr=False)
    description: str = field(repr=False)
    changed_fields: tuple[str, ...]


def review_card(
    source: OcrCardDraft,
    corrections: CardCorrections,
    *,
    confirmed_fields: frozenset[str],
    single_completed_operation: bool,
) -> ReviewedOcrCard:
    if source.blocked_reason:
        raise ValidationError(source.blocked_reason)
    if single_completed_operation is not True or confirmed_fields != frozenset(
        {"amount", "currency", "direction", "occurred_on"}
    ):
        raise ValidationError("Проверьте четыре обязательных поля и одну проведённую операцию.")
    text = corrections.amount
    if (
        not isinstance(text, str)
        or len(text) > 80
        or re.fullmatch(
            r"(?:[0-9]+|[0-9]{1,3}(?:[ \u00a0\u202f][0-9]{3})+)(?:[,.][0-9]{1,2})?",
            text.strip(),
        )
        is None
    ):
        raise ValidationError("Укажите положительную сумму без знака, не более двух знаков копеек.")
    amount = Money.parse(text.strip().replace("\u00a0", "").replace("\u202f", ""))
    if amount.minor <= 0 or corrections.currency != "RUB":
        raise ValidationError("Проверьте положительную сумму и валюту RUB.")
    if not isinstance(corrections.direction, CashDirection):
        raise ValidationError("Выберите поступление или списание.")
    try:
        if not re.fullmatch(r"[0-9]{2}\.[0-9]{2}\.[0-9]{4}", corrections.occurred_on):
            raise ValueError
        occurred_on = datetime.strptime(corrections.occurred_on, "%d.%m.%Y").date()
        local_time = None
        if corrections.local_time:
            if not re.fullmatch(r"[0-9]{2}:[0-9]{2}", corrections.local_time):
                raise ValueError
            local_time = datetime.strptime(corrections.local_time, "%H:%M").time()
    except (TypeError, ValueError):
        raise ValidationError(
            "Укажите полную дату ДД.ММ.ГГГГ и время ЧЧ:ММ либо оставьте время пустым."
        ) from None
    if (
        not isinstance(corrections.description, str)
        or len(corrections.description) > 500
        or any(not c.isprintable() and c not in "\u00a0\u202f" for c in corrections.description)
    ):
        raise ValidationError("Описание должно содержать не более 500 печатных символов.")
    values = {
        "amount": amount.minor,
        "currency": corrections.currency,
        "direction": corrections.direction,
        "occurred_on": occurred_on,
        "local_time": local_time,
        "description": corrections.description,
    }
    changed = tuple(name for name, value in values.items() if value != getattr(source, name).value)
    return ReviewedOcrCard(
        source,
        amount,
        corrections.direction,
        occurred_on,
        local_time,
        corrections.description,
        changed,
    )


@dataclass(frozen=True, slots=True)
class OcrHistoryDraft:
    document: OcrDocument = field(repr=False)
    cards: tuple[OcrCardDraft, ...] = field(repr=False)
    unassigned_fragments: int = 0
    blocked_reason: str = ""

    def __post_init__(self) -> None:
        if (
            not isinstance(self.document, OcrDocument)
            or not isinstance(self.cards, tuple)
            or len(self.cards) > 100
            or any(
                not isinstance(card, OcrCardDraft) or card.document != self.document
                for card in self.cards
            )
            or type(self.unassigned_fragments) is not int
            or self.unassigned_fragments < 0
        ):
            raise ValidationError("Некорректная история OCR.")
