"""Правила категории: версии справочника и подсказки, без записи финансовых фактов."""

from collections.abc import Callable
from dataclasses import dataclass
from threading import RLock
from typing import Protocol

from personal_finance.domain.classification import (
    CategoryRule,
    CategoryRuleVersion,
    CategorySuggestion,
    ClassificationInput,
    ClassificationSnapshot,
)
from personal_finance.domain.imports import ImportPreview, ImportStatus
from personal_finance.domain.ledger import OperationKind


@dataclass(frozen=True, slots=True)
class PreparedImportCategories:
    decisions: tuple[tuple[int, CategorySuggestion], ...]
    rule_names: tuple[tuple[str, str], ...]


def prepare_import_categories(
    snapshot: ClassificationSnapshot,
    preview: ImportPreview,
    expense_category_id: str | None,
    income_category_id: str | None,
) -> PreparedImportCategories:
    decisions = []
    for line in preview.lines:
        if line.status not in (ImportStatus.NEW, ImportStatus.POSSIBLE):
            continue
        kind = OperationKind.INCOME if line.row.amount.minor > 0 else OperationKind.EXPENSE
        manual = income_category_id if kind == OperationKind.INCOME else expense_category_id
        decisions.append(
            (
                line.row.position,
                snapshot.suggest(
                    ClassificationInput(
                        kind, preview.account_id, line.row.note, manual_category_id=manual
                    )
                ),
            )
        )
    return PreparedImportCategories(
        tuple(decisions), tuple((rule.id, rule.name) for rule in snapshot.rules)
    )


class ClassificationRepository(Protocol):
    def rules(self, *, include_disabled: bool = False) -> list[CategoryRuleVersion]: ...
    def save(
        self,
        rule: CategoryRule,
        *,
        expected_revision_id: str | None = None,
        command_id: str | None = None,
    ) -> CategoryRuleVersion: ...
    def history(self, identifier: str) -> list[CategoryRuleVersion]: ...
    def suggest(self, value: ClassificationInput) -> CategorySuggestion: ...
    def snapshot(self) -> ClassificationSnapshot: ...
    def check_integrity(self) -> None: ...


class Classification:
    def __init__(
        self, repository: ClassificationRepository, mutex: RLock, ensure_open: Callable[[], None]
    ) -> None:
        self._repository, self._mutex, self._ensure_open = repository, mutex, ensure_open

    def rules(self, *, include_disabled: bool = False) -> list[CategoryRuleVersion]:
        with self._mutex:
            self._ensure_open()
            return self._repository.rules(include_disabled=include_disabled)

    def save(
        self,
        rule: CategoryRule,
        *,
        expected_revision_id: str | None = None,
        command_id: str | None = None,
    ) -> CategoryRuleVersion:
        with self._mutex:
            self._ensure_open()
            return self._repository.save(
                rule, expected_revision_id=expected_revision_id, command_id=command_id
            )

    def history(self, identifier: str) -> list[CategoryRuleVersion]:
        with self._mutex:
            self._ensure_open()
            return self._repository.history(identifier)

    def suggest(self, value: ClassificationInput) -> CategorySuggestion:
        with self._mutex:
            self._ensure_open()
            return self._repository.suggest(value)

    def snapshot(self) -> ClassificationSnapshot:
        with self._mutex:
            self._ensure_open()
            return self._repository.snapshot()

    def check_integrity(self) -> None:
        with self._mutex:
            self._ensure_open()
            self._repository.check_integrity()
