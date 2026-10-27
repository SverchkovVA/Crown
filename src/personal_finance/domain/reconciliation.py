"""Сверка остатка из выписки с точным снимком журнала, без записи данных."""

from dataclasses import dataclass
from datetime import date

from personal_finance.domain.errors import ValidationError, VaultError
from personal_finance.domain.ledger import LedgerRole, Operation, require_identifier
from personal_finance.domain.models import Money


@dataclass(frozen=True, slots=True)
class ReconciliationLine:
    operation: Operation
    change: Money


@dataclass(frozen=True, slots=True)
class Reconciliation:
    account_id: str
    as_of: date
    book_balance: Money
    statement_balance: Money
    lines: tuple[ReconciliationLine, ...]

    @property
    def difference_minor(self) -> int:
        return self.statement_balance.minor - self.book_balance.minor

    @property
    def matches(self) -> bool:
        return self.difference_minor == 0


def reconcile_snapshot(
    account_id: str,
    as_of: date,
    book_balance: Money,
    statement_balance: Money,
    operations: list[Operation],
) -> Reconciliation:
    require_identifier(account_id)
    if (
        type(as_of) is not date
        or not isinstance(statement_balance, Money)
        or not isinstance(book_balance, Money)
    ):
        raise ValidationError("Укажите дату сверки и точный остаток из выписки.")
    if book_balance.currency != statement_balance.currency:
        raise ValidationError("Валюта выписки должна совпадать с валютой счёта.")
    lines = []
    for operation in sorted(operations, key=lambda value: (value.draft.occurred_on, value.id)):
        if operation.is_deleted or operation.draft.occurred_on > as_of:
            continue
        postings = [
            p
            for p in operation.draft.postings()
            if p.role == LedgerRole.ASSET and p.reference_id == account_id
        ]
        if not postings:
            continue
        if any(p.amount.currency != book_balance.currency for p in postings):
            raise VaultError("Валюта проводок не соответствует счёту сверки.")
        change = Money(sum(p.amount.minor for p in postings), book_balance.currency)
        lines.append(ReconciliationLine(operation, change))
    if sum(line.change.minor for line in lines) != book_balance.minor:
        raise VaultError(
            "Операции сверки не соответствуют остатку книги. Выполните проверку целостности."
        )
    return Reconciliation(account_id, as_of, book_balance, statement_balance, tuple(lines))
