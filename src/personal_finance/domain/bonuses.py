"""Бонусы программ лояльности: отдельные единицы, не денежные доходы."""

import re
from dataclasses import dataclass, field
from datetime import date
from enum import StrEnum

from personal_finance.domain.errors import ValidationError
from personal_finance.domain.ledger import require_identifier
from personal_finance.domain.models import MAX_MINOR, Money, validate_name


class BonusKind(StrEnum):
    OPENING = "opening"
    ACCRUAL = "accrual"
    SPENDING = "spending"
    EXPIRY = "expiry"
    ADJUSTMENT = "adjustment"


KIND_LABELS = {
    BonusKind.OPENING: "Начальный остаток",
    BonusKind.ACCRUAL: "Начисление",
    BonusKind.SPENDING: "Списание",
    BonusKind.EXPIRY: "Сгорание",
    BonusKind.ADJUSTMENT: "Корректировка",
}


def parse_units(value: str) -> int:
    if not isinstance(value, str):
        raise ValidationError("Введите количество бонусов текстом.")
    return Money.parse(value.replace("−", "-")).minor


def format_units(value: int) -> str:
    if type(value) is not int:
        raise ValidationError("Некорректное количество бонусов.")
    sign = "−" if value < 0 else ""
    whole, cents = divmod(abs(value), 100)
    return sign + f"{whole:,}".replace(",", " ") + f",{cents:02d}"


@dataclass(frozen=True, slots=True)
class BonusProgram:
    id: str
    revision_id: str
    name: str
    is_deleted: bool
    created_at: str


@dataclass(frozen=True, slots=True)
class BonusDraft:
    program_id: str
    occurred_on: date
    kind: BonusKind
    units_minor: int
    category_id: str | None = None
    operation_id: str | None = None
    note: str = field(default="", repr=False)
    source_key: str | None = None
    bank_category: str = field(default="", repr=False)

    def __post_init__(self) -> None:
        require_identifier(self.program_id)
        for value in (self.category_id, self.operation_id):
            if value is not None:
                require_identifier(value)
        if type(self.occurred_on) is not date or not isinstance(self.kind, BonusKind):
            raise ValidationError("Укажите дату и вид бонусной записи.")
        if type(self.units_minor) is not int or not 0 < abs(self.units_minor) <= MAX_MINOR:
            raise ValidationError("Количество бонусов должно быть ненулевым и допустимого размера.")
        if self.kind in (BonusKind.ACCRUAL, BonusKind.OPENING) and self.units_minor < 0:
            raise ValidationError("Для начисления укажите положительное количество бонусов.")
        if self.kind in (BonusKind.SPENDING, BonusKind.EXPIRY) and self.units_minor > 0:
            raise ValidationError("Списание и сгорание уменьшают количество бонусов.")
        if not isinstance(self.note, str) or len(self.note) > 500:
            raise ValidationError("Примечание должно содержать не более 500 символов.")
        if not isinstance(self.bank_category, str) or len(self.bank_category) > 120:
            raise ValidationError("Слишком длинная банковская категория.")
        if self.source_key is not None and (
            not isinstance(self.source_key, str)
            or re.fullmatch("[a-f0-9]{64}", self.source_key) is None
        ):
            raise ValidationError("Некорректный идентификатор бонусной подписи OCR.")


@dataclass(frozen=True, slots=True)
class BonusEntry:
    id: str
    revision_id: str
    draft: BonusDraft
    is_deleted: bool
    created_at: str


@dataclass(frozen=True, slots=True)
class BonusCategoryTotal:
    program_id: str
    category_id: str | None
    accrued_minor: int
    spent_minor: int


def program_name(value: str) -> str:
    return validate_name(value)
