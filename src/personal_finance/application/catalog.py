"""Сценарии справочников, независимые от SQL и шифрования."""

from datetime import UTC, datetime

from personal_finance.application.ports import CatalogRepository
from personal_finance.domain.errors import ValidationError
from personal_finance.domain.models import (
    Account,
    AccountKind,
    Category,
    CategoryKind,
    Institution,
    validate_name,
)


def utc_now() -> str:
    return datetime.now(UTC).isoformat(timespec="microseconds").replace("+00:00", "Z")


class Catalog:
    def __init__(self, repository: CatalogRepository) -> None:
        self._repository = repository

    def add_institution(self, name: str, *, command_id: str | None = None) -> Institution:
        name = validate_name(name)
        entity_id, _ = self._repository.create_revision("institution", {"name": name}, command_id)
        return Institution(entity_id, name)

    def add_account(
        self,
        name: str,
        kind: AccountKind,
        institution_id: str | None = None,
        *,
        command_id: str | None = None,
    ) -> Account:
        name = validate_name(name)
        if not isinstance(kind, AccountKind):
            raise ValidationError("Неизвестный тип счёта.")
        if kind == AccountKind.CASH and institution_id is not None:
            raise ValidationError("Наличные не могут принадлежать банковскому счёту.")
        payload = {
            "name": name,
            "kind": kind.value,
            "institution_id": institution_id,
            "currency": "RUB",
        }
        entity_id, revision_id = self._repository.create_revision("account", payload, command_id)
        return Account(entity_id, name, kind, institution_id, revision_id=revision_id)

    def add_category(
        self,
        name: str,
        kind: CategoryKind,
        parent_id: str | None = None,
        *,
        command_id: str | None = None,
    ) -> Category:
        name = validate_name(name)
        if not isinstance(kind, CategoryKind):
            raise ValidationError("Неизвестный тип категории.")
        payload = {"name": name, "kind": kind.value, "parent_id": parent_id}
        entity_id, revision_id = self._repository.create_revision("category", payload, command_id)
        return Category(entity_id, name, kind, parent_id, revision_id)

    def institutions(self) -> list[Institution]:
        return self._repository.institutions()

    def accounts(self) -> list[Account]:
        return self._repository.accounts()

    def categories(self) -> list[Category]:
        return self._repository.categories()

    def check_integrity(self) -> None:
        self._repository.check_integrity()

    def seed(self) -> None:
        for name in ("Т-Банк", "ВТБ", "Яндекс Пэй"):
            self.add_institution(name)
        for name in ("Продукты", "Транспорт", "Дом", "Здоровье", "Отдых", "Другие расходы"):
            self.add_category(name, CategoryKind.EXPENSE)
        for name in ("Зарплата", "Другие доходы"):
            self.add_category(name, CategoryKind.INCOME)
