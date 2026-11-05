"""Прикладной интерфейс отдельного бонусного журнала."""

from datetime import date
from typing import Protocol

from personal_finance.domain.bonuses import BonusCategoryTotal, BonusDraft, BonusEntry, BonusProgram
from personal_finance.domain.errors import ValidationError


class BonusRepository(Protocol):
    def programs(self, *, include_archived: bool = False) -> list[BonusProgram]: ...
    def entries(self, *, include_deleted: bool = False) -> list[BonusEntry]: ...
    def save_program(
        self,
        name: str,
        *,
        identifier: str | None = None,
        expected_revision_id: str | None = None,
        is_deleted: bool = False,
        command_id: str | None = None,
    ) -> BonusProgram: ...
    def save_entry(
        self,
        draft: BonusDraft,
        *,
        identifier: str | None = None,
        expected_revision_id: str | None = None,
        is_deleted: bool = False,
        command_id: str | None = None,
    ) -> BonusEntry: ...
    def save_entries(
        self, drafts: tuple[BonusDraft, ...], *, command_id: str | None = None
    ) -> tuple[BonusEntry, ...]: ...
    def history(self, identifier: str) -> list[BonusProgram | BonusEntry]: ...
    def check_integrity(self) -> None: ...


class Bonuses:
    def __init__(self, repository: BonusRepository) -> None:
        self._repository = repository

    def programs(self, *, include_archived: bool = False) -> list[BonusProgram]:
        return self._repository.programs(include_archived=include_archived)

    def entries(self, *, include_deleted: bool = False) -> list[BonusEntry]:
        return self._repository.entries(include_deleted=include_deleted)

    def save_program(
        self,
        name: str,
        *,
        identifier: str | None = None,
        expected_revision_id: str | None = None,
        is_deleted: bool = False,
        command_id: str | None = None,
    ) -> BonusProgram:
        return self._repository.save_program(
            name,
            identifier=identifier,
            expected_revision_id=expected_revision_id,
            is_deleted=is_deleted,
            command_id=command_id,
        )

    def save_entry(
        self,
        draft: BonusDraft,
        *,
        identifier: str | None = None,
        expected_revision_id: str | None = None,
        is_deleted: bool = False,
        command_id: str | None = None,
    ) -> BonusEntry:
        return self._repository.save_entry(
            draft,
            identifier=identifier,
            expected_revision_id=expected_revision_id,
            is_deleted=is_deleted,
            command_id=command_id,
        )

    def save_entries(
        self, drafts: tuple[BonusDraft, ...], *, command_id: str | None = None
    ) -> tuple[BonusEntry, ...]:
        return self._repository.save_entries(drafts, command_id=command_id)

    def history(self, identifier: str) -> list[BonusProgram | BonusEntry]:
        return self._repository.history(identifier)

    def balances(self) -> dict[str, int]:
        result = {p.id: 0 for p in self.programs(include_archived=True)}
        for entry in self.entries():
            result[entry.draft.program_id] += entry.draft.units_minor
        return result

    def category_report(self, start: date, end: date) -> tuple[BonusCategoryTotal, ...]:
        if type(start) is not date or type(end) is not date or start > end:
            raise ValidationError("Укажите допустимый период бонусного отчёта.")
        grouped: dict[tuple[str, str | None], list[int]] = {}
        from personal_finance.domain.bonuses import BonusKind

        for entry in self.entries():
            d = entry.draft
            if not start <= d.occurred_on <= end or d.kind not in (
                BonusKind.ACCRUAL,
                BonusKind.SPENDING,
            ):
                continue
            totals = grouped.setdefault((d.program_id, d.category_id), [0, 0])
            totals[0 if d.kind is BonusKind.ACCRUAL else 1] += abs(d.units_minor)
        return tuple(
            BonusCategoryTotal(program, category, values[0], values[1])
            for (program, category), values in sorted(
                grouped.items(), key=lambda v: (v[0][0], v[0][1] or "")
            )
        )

    def check_integrity(self) -> None:
        self._repository.check_integrity()
