"""Подсказки банковской категории и бонусов: не денежные проводки."""

import re
from dataclasses import dataclass, field

from personal_finance.domain.errors import ValidationError
from personal_finance.domain.models import MAX_MINOR


@dataclass(frozen=True, slots=True)
class OcrBonusSuggestion:
    units_minor: int = field(repr=False)
    word_indices: tuple[int, ...]
    source_key: str
    unit_hint: str = ""

    def __post_init__(self) -> None:
        if type(self.units_minor) is not int or not 0 < abs(self.units_minor) <= MAX_MINOR:
            raise ValidationError("Некорректное количество бонусной подписи.")
        if (
            not isinstance(self.word_indices, tuple)
            or not self.word_indices
            or any(type(i) is not int or i < 0 for i in self.word_indices)
        ):
            raise ValidationError("Некорректная область бонусной подписи.")
        if (
            not isinstance(self.source_key, str)
            or re.fullmatch("[a-f0-9]{64}", self.source_key) is None
        ):
            raise ValidationError("Некорректный идентификатор бонусной подписи.")
        if not isinstance(self.unit_hint, str) or len(self.unit_hint) > 16:
            raise ValidationError("Некорректная подпись единиц бонусов.")


@dataclass(frozen=True, slots=True)
class OcrOperationContext:
    bank_category: str = field(default="", repr=False)
    category_indices: tuple[int, ...] = ()
    bonuses: tuple[OcrBonusSuggestion, ...] = ()

    def __post_init__(self) -> None:
        if not isinstance(self.bank_category, str) or len(self.bank_category) > 120:
            raise ValidationError("Некорректная банковская категория.")
        if not isinstance(self.category_indices, tuple) or any(
            type(i) is not int or i < 0 for i in self.category_indices
        ):
            raise ValidationError("Некорректная область банковской категории.")
        if (
            not isinstance(self.bonuses, tuple)
            or len(self.bonuses) > 100
            or any(not isinstance(v, OcrBonusSuggestion) for v in self.bonuses)
        ):
            raise ValidationError("Некорректные бонусные подсказки.")
