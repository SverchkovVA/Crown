"""Календарные месяцы и неизменяемые решения об их закрытии."""

import calendar
from dataclasses import dataclass
from datetime import date

from personal_finance.domain.errors import ValidationError


@dataclass(frozen=True, slots=True, order=True)
class AccountingMonth:
    year: int
    month: int

    def __post_init__(self) -> None:
        if (
            type(self.year) is not int
            or type(self.month) is not int
            or not (1 <= self.year <= 9999 and 1 <= self.month <= 12)
        ):
            raise ValidationError("Укажите корректный календарный месяц.")

    @classmethod
    def from_date(cls, value: date) -> "AccountingMonth":
        if type(value) is not date:
            raise ValidationError("Укажите календарную дату.")
        return cls(value.year, value.month)

    @classmethod
    def parse(cls, value: str) -> "AccountingMonth":
        try:
            result = cls(int(value[:4]), int(value[5:]))
            if result.key != value:
                raise ValueError
            return result
        except (ValueError, TypeError, IndexError) as exc:
            raise ValidationError("Укажите месяц в формате ГГГГ-ММ.") from exc

    @property
    def key(self) -> str:
        return f"{self.year:04d}-{self.month:02d}"

    @property
    def title(self) -> str:
        return f"{self.month:02d}.{self.year:04d}"

    @property
    def start(self) -> date:
        return date(self.year, self.month, 1)

    @property
    def end(self) -> date:
        return date(self.year, self.month, calendar.monthrange(self.year, self.month)[1])


def validate_period_change(month: AccountingMonth, is_closed: bool, reason: str) -> str:
    if not isinstance(month, AccountingMonth) or type(is_closed) is not bool:
        raise ValidationError("Некорректное изменение периода.")
    if (
        not isinstance(reason, str)
        or len(reason) > 500
        or any(ord(char) < 32 and char != "\n" for char in reason)
    ):
        raise ValidationError("Причина должна содержать не более 500 печатных символов.")
    reason = reason.strip()
    if not is_closed and not reason:
        raise ValidationError("Укажите причину повторного открытия периода.")
    return reason


@dataclass(frozen=True, slots=True)
class PeriodRevision:
    id: str
    revision_id: str
    month: AccountingMonth
    is_closed: bool
    reason: str
    created_at: str
