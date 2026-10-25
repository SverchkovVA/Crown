"""Базовые сущности. Проводки и операции реализуются на этапе 4."""

from dataclasses import dataclass
from decimal import Decimal, InvalidOperation, localcontext
from enum import StrEnum

from personal_finance.domain.errors import ValidationError

MAX_MINOR = 2**63 - 1


@dataclass(frozen=True, slots=True)
class Currency:
    code: str
    exponent: int

    def __post_init__(self) -> None:
        if (
            len(self.code) != 3
            or not self.code.isascii()
            or not self.code.isalpha()
            or self.code != self.code.upper()
            or type(self.exponent) is not int
            or not 0 <= self.exponent <= 8
        ):
            raise ValidationError("Некорректное описание валюты.")


RUB = Currency("RUB", 2)


@dataclass(frozen=True, slots=True)
class Money:
    minor: int
    currency: Currency = RUB

    def __post_init__(self) -> None:
        if (
            type(self.minor) is not int
            or abs(self.minor) > MAX_MINOR
            or not isinstance(self.currency, Currency)
        ):
            raise ValidationError("Денежная сумма выходит за допустимый диапазон.")

    @classmethod
    def parse(cls, text: str, currency: Currency = RUB) -> "Money":
        try:
            if len(text) > 128:
                raise ValidationError("Слишком длинная запись денежной суммы.")
            with localcontext() as context:
                context.prec = 40
                amount = Decimal(text.strip().replace(" ", "").replace(",", "."))
                if not amount.is_finite() or amount.copy_abs() > MAX_MINOR:
                    raise ValidationError("Укажите конечную денежную сумму.")
                parts = amount.as_tuple()
                exponent = int(parts.exponent)
                extra = -exponent - currency.exponent
                if extra > 0 and any(parts.digits[-extra:]):
                    raise ValidationError("У суммы слишком много знаков после запятой.")
                scaled = amount * (10**currency.exponent)
                if scaled != scaled.to_integral_value():
                    raise ValidationError("У суммы слишком много знаков после запятой.")
                return cls(int(scaled), currency)
        except (InvalidOperation, ValueError) as exc:
            raise ValidationError("Не удалось прочитать денежную сумму.") from exc

    def __add__(self, other: "Money") -> "Money":
        if self.currency != other.currency:
            raise ValidationError("Нельзя складывать суммы разных валют.")
        return Money(self.minor + other.minor, self.currency)


class AccountKind(StrEnum):
    DEBIT = "debit"
    SAVINGS = "savings"
    CASH = "cash"
    INVESTMENT = "investment"


ACCOUNT_LABELS = {
    AccountKind.DEBIT: "Банковский счёт",
    AccountKind.SAVINGS: "Накопительный счёт",
    AccountKind.CASH: "Наличные",
    AccountKind.INVESTMENT: "Инвестиционный счёт",
}


class CategoryKind(StrEnum):
    INCOME = "income"
    EXPENSE = "expense"


@dataclass(frozen=True, slots=True)
class Institution:
    id: str
    name: str


@dataclass(frozen=True, slots=True)
class Account:
    id: str
    name: str
    kind: AccountKind
    institution_id: str | None
    currency: str = "RUB"
    revision_id: str = ""


@dataclass(frozen=True, slots=True)
class Category:
    id: str
    name: str
    kind: CategoryKind
    parent_id: str | None = None
    revision_id: str = ""


def validate_name(value: str) -> str:
    value = value.strip()
    if not value or len(value) > 120 or any(ord(char) < 32 for char in value):
        raise ValidationError("Название должно содержать от 1 до 120 печатных символов.")
    return value
