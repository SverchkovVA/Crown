"""Версии тегов и сохранённых фильтров локальной книги."""

from dataclasses import dataclass

from personal_finance.domain.query import OperationQuery


@dataclass(frozen=True, slots=True)
class Tag:
    id: str
    revision_id: str
    name: str
    is_deleted: bool
    created_at: str


@dataclass(frozen=True, slots=True)
class SavedFilter:
    id: str
    revision_id: str
    name: str
    query: OperationQuery
    is_deleted: bool
    created_at: str
