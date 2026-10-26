"""Финансовые операции и сбалансированные проводки, без SQL и интерфейса."""

from dataclasses import dataclass, field
from datetime import date
from enum import StrEnum
from uuid import UUID

from personal_finance.domain.errors import ValidationError
from personal_finance.domain.models import MAX_MINOR, Money


class OperationKind(StrEnum):
    OPENING = "opening"
    INCOME = "income"
    EXPENSE = "expense"
    TRANSFER = "transfer"


OPERATION_LABELS = {
    OperationKind.OPENING: "Начальный остаток",
    OperationKind.INCOME: "Доход",
    OperationKind.EXPENSE: "Расход",
    OperationKind.TRANSFER: "Перевод",
}


class LedgerRole(StrEnum):
    ASSET = "asset"
    INCOME = "income"
    EXPENSE = "expense"
    OPENING = "opening"


def require_identifier(value: str) -> None:
    try:
        if not isinstance(value, str) or str(UUID(value)) != value:
            raise ValueError
    except (ValueError, TypeError, AttributeError) as exc:
        raise ValidationError("Некорректный идентификатор записи.") from exc


@dataclass(frozen=True, slots=True)
class Posting:
    role: LedgerRole
    reference_id: str | None
    amount: Money


def require_balanced(postings: tuple[Posting, ...]) -> None:
    if len(postings) < 2:
        raise ValidationError("Операция должна содержать не менее двух проводок.")
    totals: dict[object, int] = {}
    for posting in postings:
        totals[posting.amount.currency] = (
            totals.get(posting.amount.currency, 0) + posting.amount.minor
        )
    if any(totals.values()):
        raise ValidationError("Проводки операции не сбалансированы по валютам.")


@dataclass(frozen=True, slots=True)
class OperationDraft:
    kind: OperationKind
    occurred_on: date
    account_id: str
    amount: Money
    category_id: str | None = None
    destination_account_id: str | None = None
    fee: Money = field(default_factory=lambda: Money(0))
    fee_category_id: str | None = None
    note: str = ""

    def __post_init__(self) -> None:
        if not isinstance(self.kind, OperationKind):
            raise ValidationError("Неизвестный вид финансовой операции.")
        if type(self.occurred_on) is not date:
            raise ValidationError("Укажите календарную дату операции.")
        require_identifier(self.account_id)
        for value in (self.category_id, self.destination_account_id, self.fee_category_id):
            if value is not None:
                require_identifier(value)
        if not isinstance(self.amount, Money) or not isinstance(self.fee, Money):
            raise ValidationError("Сумма операции должна быть точным денежным значением.")
        if self.amount.currency != self.fee.currency:
            raise ValidationError("Сумма и комиссия должны иметь одну валюту.")
        if self.fee.minor < 0:
            raise ValidationError("Комиссия не может быть отрицательной.")
        if self.kind != OperationKind.OPENING and self.amount.minor <= 0:
            raise ValidationError("Сумма дохода, расхода или перевода должна быть положительной.")
        if (
            not isinstance(self.note, str)
            or len(self.note) > 500
            or any(ord(character) < 32 and character != "\n" for character in self.note)
        ):
            raise ValidationError("Примечание должно содержать не более 500 печатных символов.")
        if self.kind in (OperationKind.INCOME, OperationKind.EXPENSE):
            if self.category_id is None:
                raise ValidationError("Выберите категорию дохода или расхода.")
        elif self.category_id is not None:
            raise ValidationError("Для остатка или перевода основная категория не используется.")
        if self.kind == OperationKind.TRANSFER:
            if self.destination_account_id is None:
                raise ValidationError("Выберите счёт получения перевода.")
            if self.destination_account_id == self.account_id:
                raise ValidationError("Счета отправления и получения должны различаться.")
            if self.amount.minor + self.fee.minor > MAX_MINOR:
                raise ValidationError("Сумма перевода вместе с комиссией выходит за предел.")
            if (self.fee.minor > 0) != (self.fee_category_id is not None):
                raise ValidationError("Для ненулевой комиссии выберите расходную категорию.")
        elif (
            self.destination_account_id is not None
            or self.fee.minor != 0
            or self.fee_category_id is not None
        ):
            raise ValidationError("Счёт получения и комиссия используются только для перевода.")

    def postings(self) -> tuple[Posting, ...]:
        currency = self.amount.currency
        minor = self.amount.minor
        result: tuple[Posting, ...]
        if self.kind == OperationKind.OPENING:
            result = (
                Posting(LedgerRole.ASSET, self.account_id, self.amount),
                Posting(LedgerRole.OPENING, None, Money(-minor, currency)),
            )
        elif self.kind == OperationKind.INCOME:
            result = (
                Posting(LedgerRole.ASSET, self.account_id, self.amount),
                Posting(LedgerRole.INCOME, self.category_id, Money(-minor, currency)),
            )
        elif self.kind == OperationKind.EXPENSE:
            result = (
                Posting(LedgerRole.ASSET, self.account_id, Money(-minor, currency)),
                Posting(LedgerRole.EXPENSE, self.category_id, self.amount),
            )
        else:
            result = (
                Posting(
                    LedgerRole.ASSET, self.account_id, Money(-minor - self.fee.minor, currency)
                ),
                Posting(LedgerRole.ASSET, self.destination_account_id, self.amount),
            )
            if self.fee.minor:
                result += (Posting(LedgerRole.EXPENSE, self.fee_category_id, self.fee),)
        require_balanced(result)
        return result


@dataclass(frozen=True, slots=True)
class Operation:
    id: str
    revision_id: str
    draft: OperationDraft
    is_deleted: bool = False


@dataclass(frozen=True, slots=True)
class AccountBalance:
    account_id: str
    amount: Money
