"""Настраиваемые предложения OCR; исходные слова и поля не изменяются."""

import re
from dataclasses import dataclass, field
from datetime import date

from personal_finance.domain.errors import ValidationError
from personal_finance.domain.ocr_cards import CardCorrections, CashDirection, OcrCardDraft, OcrIssue


@dataclass(frozen=True, slots=True)
class OcrReviewDefaults:
    currency: str = "RUB"
    reference_year: int = field(default_factory=lambda: date.today().year)
    confirm_fields: bool = True

    def __post_init__(self) -> None:
        if (
            self.currency != "RUB"
            or type(self.reference_year) is not int
            or not 1 <= self.reference_year <= 9999
            or type(self.confirm_fields) is not bool
        ):
            raise ValidationError("Проверьте валюту, год и режим проверки OCR.")

    @classmethod
    def for_today(cls) -> "OcrReviewDefaults":
        return cls()


def suggest_corrections(card: OcrCardDraft, defaults: OcrReviewDefaults) -> CardCorrections:
    from personal_finance.infrastructure.ocr.rub_fields import suggest_occurred_on

    occurred = suggest_occurred_on(card, reference_year=defaults.reference_year).value
    minor = card.amount.value
    direction = card.direction.value
    if direction is None and OcrIssue.AMBIGUOUS not in card.direction.issues:
        text = " ".join(card.document.words[i].text for i in card.amount.word_indices)
        signs = re.findall(r"(?<![\w\d])([+\-−—–])\s*(?=[0-9])", text)
        if signs and all(sign in ("—", "–") for sign in signs):
            direction = CashDirection.OUT
    return CardCorrections(
        f"{minor // 100},{minor % 100:02d}" if minor is not None else "",
        card.currency.value or defaults.currency,
        direction,
        occurred.strftime("%d.%m.%Y") if occurred else "",
        card.local_time.value.strftime("%H:%M") if card.local_time.value else "",
        card.description.value or "",
    )
