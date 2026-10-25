"""Минимальная граница сценариев и адаптера хранения справочников."""

from collections.abc import Mapping
from typing import Any, Protocol

from personal_finance.domain.models import Account, Category, Institution


class CatalogRepository(Protocol):
    def create_revision(
        self, kind: str, payload: Mapping[str, Any], command_id: str | None
    ) -> tuple[str, str]: ...

    def institutions(self) -> list[Institution]: ...

    def accounts(self) -> list[Account]: ...

    def categories(self) -> list[Category]: ...

    def check_integrity(self) -> None: ...
