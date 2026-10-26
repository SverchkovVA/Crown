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
    REFUND = "refund"


OPERATION_LABELS = {
    OperationKind.OPENING: "Начальный остаток",
    OperationKind.INCOME: "Доход",
    OperationKind.EXPENSE: "Расход",
    OperationKind.TRANSFER: "Перевод",
    OperationKind.REFUND: "Возврат",
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
class OperationPart:
    category_id: str
    amount: Money

    def __post_init__(self) -> None:
        require_identifier(self.category_id)
        if not isinstance(self.amount, Money) or self.amount.minor <= 0:
            raise ValidationError("Сумма каждой части должна быть положительной.")


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
    parts: tuple[OperationPart, ...] = ()
    refund_of: str | None = None
    refund_of_revision_id: str | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.kind, OperationKind):
            raise ValidationError("Неизвестный вид финансовой операции.")
        if type(self.occurred_on) is not date:
            raise ValidationError("Укажите календарную дату операции.")
        require_identifier(self.account_id)
        for value in (
            self.category_id,
            self.destination_account_id,
            self.fee_category_id,
            self.refund_of,
            self.refund_of_revision_id,
        ):
            if value is not None:
                require_identifier(value)
        if not isinstance(self.amount, Money) or not isinstance(self.fee, Money):
            raise ValidationError("Сумма операции должна быть точным денежным значением.")
        if self.amount.currency != self.fee.currency:
            raise ValidationError("Сумма и комиссия должны иметь одну валюту.")
        if self.fee.minor < 0:
            raise ValidationError("Комиссия не может быть отрицательной.")
        if self.kind != OperationKind.OPENING and self.amount.minor <= 0:
            raise ValidationError(
                "Сумма дохода, расхода, перевода или возврата должна быть положительной."
            )
        if (
            not isinstance(self.note, str)
            or len(self.note) > 500
            or any(ord(character) < 32 and character != "\n" for character in self.note)
        ):
            raise ValidationError("Примечание должно содержать не более 500 печатных символов.")
        if not isinstance(self.parts, tuple):
            raise ValidationError("Части операции должны быть неизменяемым набором.")
        if self.parts:
            if self.kind not in (OperationKind.INCOME, OperationKind.EXPENSE, OperationKind.REFUND):
                raise ValidationError("Разделить можно доход, расход или связанный возврат.")
            if self.category_id is not None or not 2 <= len(self.parts) <= 100:
                raise ValidationError(
                    "Для разделения укажите от 2 до 100 частей без общей категории."
                )
            categories: set[str] = set()
            total = 0
            for part in self.parts:
                if not isinstance(part, OperationPart):
                    raise ValidationError("Некорректная часть финансовой операции.")
                if part.amount.currency != self.amount.currency:
                    raise ValidationError("Все части должны иметь валюту операции.")
                if part.category_id in categories:
                    raise ValidationError(
                        "Категория повторяется: объедините её суммы в одну часть."
                    )
                categories.add(part.category_id)
                total += part.amount.minor
            if total != self.amount.minor:
                raise ValidationError("Сумма частей должна точно совпадать с суммой операции.")
        if self.kind in (OperationKind.INCOME, OperationKind.EXPENSE, OperationKind.REFUND):
            if self.category_id is None and not self.parts:
                raise ValidationError("Выберите категорию дохода или расхода.")
        elif self.category_id is not None:
            raise ValidationError("Для остатка или перевода основная категория не используется.")
        if self.kind == OperationKind.REFUND:
            if self.refund_of is None or self.refund_of_revision_id is None:
                raise ValidationError("Для возврата выберите исходный расход и его версию.")
        elif self.refund_of is not None or self.refund_of_revision_id is not None:
            raise ValidationError("Связь с исходным расходом используется только для возврата.")
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

    def category_allocations(self) -> tuple[OperationPart, ...]:
        if self.parts:
            return self.parts
        if self.category_id is not None:
            return (OperationPart(self.category_id, self.amount),)
        return ()

    def postings(self) -> tuple[Posting, ...]:
        currency = self.amount.currency
        minor = self.amount.minor
        result: tuple[Posting, ...]
        if self.kind == OperationKind.OPENING:
            result = (
                Posting(LedgerRole.ASSET, self.account_id, self.amount),
                Posting(LedgerRole.OPENING, None, Money(-minor, currency)),
            )
        elif self.kind in (OperationKind.INCOME, OperationKind.REFUND):
            role = LedgerRole.INCOME if self.kind == OperationKind.INCOME else LedgerRole.EXPENSE
            result = (Posting(LedgerRole.ASSET, self.account_id, self.amount),) + tuple(
                Posting(role, part.category_id, Money(-part.amount.minor, currency))
                for part in self.category_allocations()
            )
        elif self.kind == OperationKind.EXPENSE:
            result = (Posting(LedgerRole.ASSET, self.account_id, Money(-minor, currency)),) + tuple(
                Posting(LedgerRole.EXPENSE, part.category_id, part.amount)
                for part in self.category_allocations()
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
