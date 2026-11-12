"""Команды для версионных условий планирования."""

from typing import Protocol

from personal_finance.domain.errors import ValidationError
from personal_finance.domain.planning_scenarios import (
    PlanningKind,
    PlanningScenario,
    SavedPlanningScenario,
    planning_kind,
)


class PlanningScenariosRepository(Protocol):
    def items(
        self, *, kind: PlanningKind | None = None, include_archived: bool = False
    ) -> list[SavedPlanningScenario]: ...
    def get(self, identifier: str) -> SavedPlanningScenario: ...
    def history(self, identifier: str) -> list[SavedPlanningScenario]: ...
    def save(
        self,
        name: str,
        scenario: PlanningScenario,
        *,
        identifier: str | None = None,
        expected_revision_id: str | None = None,
        is_deleted: bool = False,
        command_id: str | None = None,
    ) -> SavedPlanningScenario: ...
    def check_integrity(self) -> None: ...


class PlanningScenarios:
    def __init__(self, repository: PlanningScenariosRepository) -> None:
        self._repository = repository

    def items(
        self, *, kind: PlanningKind | None = None, include_archived: bool = False
    ) -> list[SavedPlanningScenario]:
        return self._repository.items(kind=kind, include_archived=include_archived)

    def get(self, identifier: str) -> SavedPlanningScenario:
        return self._repository.get(identifier)

    def history(self, identifier: str) -> list[SavedPlanningScenario]:
        return self._repository.history(identifier)

    def _expected(self, identifier: str, revision_id: str) -> SavedPlanningScenario:
        for value in self._repository.history(identifier):
            if value.revision_id == revision_id:
                return value
        raise ValidationError("Ожидаемая версия условий планирования не найдена в этой книге.")

    def save(
        self,
        name: str,
        scenario: PlanningScenario,
        *,
        identifier: str | None = None,
        expected_revision_id: str | None = None,
        command_id: str | None = None,
    ) -> SavedPlanningScenario:
        if (identifier is None) != (expected_revision_id is None):
            raise ValidationError("Для изменения укажите ожидаемую версию условий планирования.")
        current = (
            self._expected(identifier, expected_revision_id)
            if identifier is not None and expected_revision_id is not None
            else None
        )
        if current is not None and current.kind != planning_kind(scenario):
            raise ValidationError("Вид сохранённых условий нельзя заменить.")
        return self._repository.save(
            name,
            scenario,
            identifier=identifier,
            expected_revision_id=expected_revision_id,
            is_deleted=current.is_deleted if current is not None else False,
            command_id=command_id,
        )

    def rename(
        self,
        identifier: str,
        expected_revision_id: str,
        name: str,
        *,
        command_id: str | None = None,
    ) -> SavedPlanningScenario:
        current = self._expected(identifier, expected_revision_id)
        return self._repository.save(
            name,
            current.scenario,
            identifier=identifier,
            expected_revision_id=expected_revision_id,
            is_deleted=current.is_deleted,
            command_id=command_id,
        )

    def archive(
        self,
        identifier: str,
        expected_revision_id: str,
        *,
        archived: bool = True,
        command_id: str | None = None,
    ) -> SavedPlanningScenario:
        current = self._expected(identifier, expected_revision_id)
        return self._repository.save(
            current.name,
            current.scenario,
            identifier=identifier,
            expected_revision_id=expected_revision_id,
            is_deleted=archived,
            command_id=command_id,
        )

    def check_integrity(self) -> None:
        self._repository.check_integrity()
