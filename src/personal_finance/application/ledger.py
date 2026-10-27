"""Прикладные сценарии ручного журнала, независимо от SQL."""

from datetime import date
from typing import Protocol

from personal_finance.domain.ledger import AccountBalance, Operation, OperationDraft
from personal_finance.domain.models import Money
from personal_finance.domain.reconciliation import Reconciliation
from personal_finance.domain.refunds import RefundCandidate


class LedgerRepository(Protocol):
    supports_splits: bool
    supports_refunds: bool

    def save(
        self,
        draft: OperationDraft,
        *,
        operation_id: str | None = None,
        expected_revision_id: str | None = None,
        is_deleted: bool = False,
        command_id: str | None = None,
    ) -> Operation: ...

    def operation(self, operation_id: str) -> Operation: ...

    def operations(self, *, include_deleted: bool = False) -> list[Operation]: ...

    def history(self, operation_id: str) -> list[Operation]: ...

    def balances(self, as_of: date | None = None) -> list[AccountBalance]: ...

    def refund_candidates(
        self,
        *,
        excluding_refund_id: str | None = None,
    ) -> list[RefundCandidate]: ...

    def reconcile(
        self, account_id: str, as_of: date, statement_balance: Money
    ) -> Reconciliation: ...

    def check_integrity(self) -> None: ...


class Ledger:
    def __init__(self, repository: LedgerRepository) -> None:
        self._repository = repository

    @property
    def supports_splits(self) -> bool:
        return self._repository.supports_splits

    @property
    def supports_refunds(self) -> bool:
        return self._repository.supports_refunds

    def refund_candidates(
        self,
        *,
        excluding_refund_id: str | None = None,
    ) -> list[RefundCandidate]:
        return self._repository.refund_candidates(excluding_refund_id=excluding_refund_id)

    def add(self, draft: OperationDraft, *, command_id: str | None = None) -> Operation:
        return self._repository.save(draft, command_id=command_id)

    def replace(
        self,
        operation_id: str,
        expected_revision_id: str,
        draft: OperationDraft,
        *,
        command_id: str | None = None,
    ) -> Operation:
        return self._repository.save(
            draft,
            operation_id=operation_id,
            expected_revision_id=expected_revision_id,
            command_id=command_id,
        )

    def delete(
        self,
        operation_id: str,
        expected_revision_id: str,
        *,
        command_id: str | None = None,
    ) -> Operation:
        current = self._repository.operation(operation_id)
        return self._repository.save(
            current.draft,
            operation_id=operation_id,
            expected_revision_id=expected_revision_id,
            is_deleted=True,
            command_id=command_id,
        )

    def operations(self, *, include_deleted: bool = False) -> list[Operation]:
        return self._repository.operations(include_deleted=include_deleted)

    def history(self, operation_id: str) -> list[Operation]:
        return self._repository.history(operation_id)

    def balances(self, as_of: date | None = None) -> list[AccountBalance]:
        return self._repository.balances(as_of)

    def reconcile(self, account_id: str, as_of: date, statement_balance: Money) -> Reconciliation:
        return self._repository.reconcile(account_id, as_of, statement_balance)

    def check_integrity(self) -> None:
        self._repository.check_integrity()
