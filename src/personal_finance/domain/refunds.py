"""Связанные возвраты: ограничения источника и доступные суммы по категориям."""

from collections.abc import Sequence
from dataclasses import dataclass

from personal_finance.domain.errors import ValidationError
from personal_finance.domain.ledger import Operation, OperationDraft, OperationKind, OperationPart
from personal_finance.domain.models import Money


@dataclass(frozen=True, slots=True)
class RefundCandidate:
    expense: Operation
    remaining_parts: tuple[OperationPart, ...]

    @property
    def amount_remaining(self) -> Money:
        return Money(
            sum(part.amount.minor for part in self.remaining_parts),
            self.expense.draft.amount.currency,
        )

    @property
    def amount_refunded(self) -> Money:
        return Money(
            self.expense.draft.amount.minor - self.amount_remaining.minor,
            self.expense.draft.amount.currency,
        )


def _validate_against_expense(refund: OperationDraft, expense: Operation) -> None:
    source = expense.draft
    if expense.is_deleted or source.kind != OperationKind.EXPENSE:
        raise ValidationError("Возврат должен быть связан с действующим расходом.")
    if refund.amount.currency != source.amount.currency:
        raise ValidationError("Валюта возврата должна совпадать с валютой расхода.")
    if refund.occurred_on < source.occurred_on:
        raise ValidationError("Дата возврата не может быть раньше исходного расхода.")
    limits = {part.category_id: part.amount.minor for part in source.category_allocations()}
    for part in refund.category_allocations():
        if part.category_id not in limits or part.amount.minor > limits[part.category_id]:
            raise ValidationError("Сумма возврата превышает расход по одной из категорий.")


def validate_refund_reference(refund: OperationDraft, source_version: Operation) -> None:
    if (
        refund.kind != OperationKind.REFUND
        or refund.refund_of != source_version.id
        or refund.refund_of_revision_id != source_version.revision_id
    ):
        raise ValidationError("Исходный расход и его версия не соответствуют возврату.")
    _validate_against_expense(refund, source_version)


def calculate_refund_candidates(
    operations: Sequence[Operation],
    *,
    excluding_refund_id: str | None = None,
) -> list[RefundCandidate]:
    active = [operation for operation in operations if not operation.is_deleted]
    if excluding_refund_id is not None and not any(
        operation.id == excluding_refund_id and operation.draft.kind == OperationKind.REFUND
        for operation in active
    ):
        raise ValidationError("Возврат для исправления не найден в этой книге.")
    expenses = {
        operation.id: operation
        for operation in active
        if operation.draft.kind == OperationKind.EXPENSE
    }
    returned: dict[tuple[str, str], int] = {}
    for operation in active:
        value = operation.draft
        if value.kind != OperationKind.REFUND or operation.id == excluding_refund_id:
            continue
        source = expenses.get(value.refund_of or "")
        if source is None:
            raise ValidationError("Нельзя удалить или изменить вид расхода с активными возвратами.")
        _validate_against_expense(value, source)
        limits = {
            part.category_id: part.amount.minor for part in source.draft.category_allocations()
        }
        for part in value.category_allocations():
            key = (source.id, part.category_id)
            returned[key] = returned.get(key, 0) + part.amount.minor
            if returned[key] > limits[part.category_id]:
                raise ValidationError(
                    "Общая сумма возвратов превышает расход по одной из категорий."
                )
    result = []
    for expense in expenses.values():
        remaining = tuple(
            OperationPart(
                part.category_id,
                Money(
                    part.amount.minor - returned.get((expense.id, part.category_id), 0),
                    part.amount.currency,
                ),
            )
            for part in expense.draft.category_allocations()
            if part.amount.minor > returned.get((expense.id, part.category_id), 0)
        )
        result.append(RefundCandidate(expense, remaining))
    return result
