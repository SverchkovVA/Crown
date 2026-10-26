"""Явное закрытие и открытие месяцев без изменения финансовых фактов."""

from typing import Protocol

from personal_finance.domain.periods import AccountingMonth, PeriodRevision


class PeriodRepository(Protocol):
    def states(self) -> list[PeriodRevision]: ...
    def history(self, month: AccountingMonth) -> list[PeriodRevision]: ...
    def set_state(
        self,
        month: AccountingMonth,
        is_closed: bool,
        *,
        expected_revision_id: str | None,
        reason: str,
        command_id: str | None,
    ) -> PeriodRevision: ...
    def check_integrity(self) -> None: ...


class Periods:
    def __init__(self, repository: PeriodRepository) -> None:
        self._repository = repository

    def states(self) -> list[PeriodRevision]:
        return self._repository.states()

    def history(self, month: AccountingMonth) -> list[PeriodRevision]:
        return self._repository.history(month)

    def close(
        self,
        month: AccountingMonth,
        *,
        expected_revision_id: str | None = None,
        reason: str = "",
        command_id: str | None = None,
    ) -> PeriodRevision:
        return self._repository.set_state(
            month,
            True,
            expected_revision_id=expected_revision_id,
            reason=reason,
            command_id=command_id,
        )

    def reopen(
        self,
        month: AccountingMonth,
        expected_revision_id: str,
        *,
        reason: str,
        command_id: str | None = None,
    ) -> PeriodRevision:
        return self._repository.set_state(
            month,
            False,
            expected_revision_id=expected_revision_id,
            reason=reason,
            command_id=command_id,
        )

    def check_integrity(self) -> None:
        self._repository.check_integrity()
