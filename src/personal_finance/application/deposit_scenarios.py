"""Команды неизменяемых сценариев; фактические операции не создаются."""

from typing import Protocol

from personal_finance.domain.deposit_scenarios import SavedDepositScenario
from personal_finance.domain.deposits import DepositScenario
from personal_finance.domain.errors import ValidationError


class DepositScenariosRepository(Protocol):
    def items(self, *, include_archived: bool = False) -> list[SavedDepositScenario]: ...
    def get(self, identifier: str) -> SavedDepositScenario: ...
    def history(self, identifier: str) -> list[SavedDepositScenario]: ...
    def save(
        self,
        name: str,
        scenario: DepositScenario,
        *,
        identifier: str | None = None,
        expected_revision_id: str | None = None,
        is_deleted: bool = False,
        command_id: str | None = None,
    ) -> SavedDepositScenario: ...
    def check_integrity(self) -> None: ...


class DepositScenarios:
    def __init__(self, repository: DepositScenariosRepository) -> None:
        self._repository = repository

    def items(self, *, include_archived: bool = False) -> list[SavedDepositScenario]:
        return self._repository.items(include_archived=include_archived)

    def get(self, identifier: str) -> SavedDepositScenario:
        return self._repository.get(identifier)

    def history(self, identifier: str) -> list[SavedDepositScenario]:
        return self._repository.history(identifier)

    def _expected(self, identifier: str, revision_id: str) -> SavedDepositScenario:
        for value in self._repository.history(identifier):
            if value.revision_id == revision_id:
                return value
        raise ValidationError("Ожидаемая версия сценария вклада не найдена в этой книге.")

    def save(
        self,
        name: str,
        scenario: DepositScenario,
        *,
        identifier: str | None = None,
        expected_revision_id: str | None = None,
        command_id: str | None = None,
    ) -> SavedDepositScenario:
        if (identifier is None) != (expected_revision_id is None):
            raise ValidationError("Для изменения укажите ожидаемую версию сценария вклада.")
        current = (
            self._expected(identifier, expected_revision_id)
            if identifier is not None and expected_revision_id is not None
            else None
        )
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
    ) -> SavedDepositScenario:
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
    ) -> SavedDepositScenario:
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
