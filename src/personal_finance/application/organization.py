"""Прикладные команды тегов и сохранённых фильтров."""

from typing import Protocol

from personal_finance.domain.errors import ValidationError
from personal_finance.domain.organization import SavedFilter, Tag
from personal_finance.domain.query import OperationQuery


class OrganizationRepository(Protocol):
    def tags(self, *, include_archived: bool = False) -> list[Tag]: ...
    def filters(self, *, include_archived: bool = False) -> list[SavedFilter]: ...
    def tag(self, identifier: str) -> Tag: ...
    def saved_filter(self, identifier: str) -> SavedFilter: ...
    def save_tag(
        self,
        name: str,
        *,
        identifier: str | None = None,
        expected_revision_id: str | None = None,
        is_deleted: bool = False,
        command_id: str | None = None,
    ) -> Tag: ...
    def save_filter(
        self,
        name: str,
        query: OperationQuery,
        *,
        identifier: str | None = None,
        expected_revision_id: str | None = None,
        is_deleted: bool = False,
        command_id: str | None = None,
    ) -> SavedFilter: ...
    def history(self, kind: str, identifier: str) -> list[Tag | SavedFilter]: ...
    def check_integrity(self) -> None: ...


class Organization:
    def __init__(self, repository: OrganizationRepository) -> None:
        self._repository = repository

    def _expected(self, kind: str, identifier: str, revision_id: str) -> Tag | SavedFilter:
        for value in self._repository.history(kind, identifier):
            if value.revision_id == revision_id:
                return value
        raise ValidationError("Ожидаемая версия тега или фильтра не найдена в этой книге.")

    def tags(self, *, include_archived: bool = False) -> list[Tag]:
        return self._repository.tags(include_archived=include_archived)

    def filters(self, *, include_archived: bool = False) -> list[SavedFilter]:
        return self._repository.filters(include_archived=include_archived)

    def add_tag(self, name: str, *, command_id: str | None = None) -> Tag:
        return self._repository.save_tag(name, command_id=command_id)

    def rename_tag(
        self,
        identifier: str,
        expected_revision_id: str,
        name: str,
        *,
        command_id: str | None = None,
    ) -> Tag:
        current = self._expected("tag", identifier, expected_revision_id)
        return self._repository.save_tag(
            name,
            identifier=identifier,
            expected_revision_id=expected_revision_id,
            is_deleted=current.is_deleted,
            command_id=command_id,
        )

    def archive_tag(
        self,
        identifier: str,
        expected_revision_id: str,
        *,
        archived: bool = True,
        command_id: str | None = None,
    ) -> Tag:
        current = self._expected("tag", identifier, expected_revision_id)
        return self._repository.save_tag(
            current.name,
            identifier=identifier,
            expected_revision_id=expected_revision_id,
            is_deleted=archived,
            command_id=command_id,
        )

    def save_filter(
        self,
        name: str,
        query: OperationQuery,
        *,
        identifier: str | None = None,
        expected_revision_id: str | None = None,
        command_id: str | None = None,
    ) -> SavedFilter:
        return self._repository.save_filter(
            name,
            query,
            identifier=identifier,
            expected_revision_id=expected_revision_id,
            command_id=command_id,
        )

    def rename_filter(
        self,
        identifier: str,
        expected_revision_id: str,
        name: str,
        *,
        command_id: str | None = None,
    ) -> SavedFilter:
        current = self._expected("saved_filter", identifier, expected_revision_id)
        assert isinstance(current, SavedFilter)
        return self._repository.save_filter(
            name,
            current.query,
            identifier=identifier,
            expected_revision_id=expected_revision_id,
            is_deleted=current.is_deleted,
            command_id=command_id,
        )

    def archive_filter(
        self,
        identifier: str,
        expected_revision_id: str,
        *,
        archived: bool = True,
        command_id: str | None = None,
    ) -> SavedFilter:
        current = self._expected("saved_filter", identifier, expected_revision_id)
        assert isinstance(current, SavedFilter)
        return self._repository.save_filter(
            current.name,
            current.query,
            identifier=identifier,
            expected_revision_id=expected_revision_id,
            is_deleted=archived,
            command_id=command_id,
        )

    def history(self, kind: str, identifier: str) -> list[Tag | SavedFilter]:
        return self._repository.history(kind, identifier)

    def check_integrity(self) -> None:
        self._repository.check_integrity()
