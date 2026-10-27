"""Согласованный снимок журнала и отображаемых названий, без записи в книгу."""

from dataclasses import replace
from threading import RLock

from sqlalchemy import Connection, Engine, bindparam, text

from personal_finance.domain.errors import ValidationError, VaultError
from personal_finance.domain.exporting import (
    ExportLabels,
    ExportPreview,
    ExportSettings,
    prepare_journal_export,
)
from personal_finance.domain.ledger import Operation
from personal_finance.infrastructure.persistence.ledger_repository import SqlLedgerRepository


class SqlExportRepository:
    def __init__(
        self, engine: Engine, book_id: str, mutex: RLock, ledger: SqlLedgerRepository
    ) -> None:
        self._engine, self._book_id, self._mutex, self._ledger = engine, book_id, mutex, ledger

    def _read_labels(
        self, connection: Connection, operations: tuple[Operation, ...], include_tags: bool
    ) -> ExportLabels:
        accounts = {op.draft.account_id for op in operations}
        accounts.update(
            op.draft.destination_account_id
            for op in operations
            if op.draft.destination_account_id is not None
        )
        categories = {
            part.category_id for op in operations for part in op.draft.category_allocations()
        }
        categories.update(
            op.draft.fee_category_id for op in operations if op.draft.fee_category_id is not None
        )
        tags = {tag for op in operations for tag in op.draft.tag_ids} if include_tags else set()
        account_labels: list[tuple[str, str]] = []
        bank_labels: list[tuple[str, str]] = []
        if accounts:
            query = text("""SELECT e.id,a.name,b.name AS bank_name FROM entities e
                JOIN account_revisions a ON a.revision_id=e.accepted_revision_id
                LEFT JOIN entities bank ON bank.id=a.institution_id
                AND bank.book_id=e.book_id AND bank.kind='institution'
                LEFT JOIN institution_revisions b ON b.revision_id=bank.accepted_revision_id
                WHERE e.book_id=:book AND e.kind='account' AND e.id IN :ids
                ORDER BY e.id""").bindparams(bindparam("ids", expanding=True))
            for row in connection.execute(
                query, {"book": self._book_id, "ids": sorted(accounts)}
            ).mappings():
                account_labels.append((row["id"], row["name"]))
                bank_labels.append((row["id"], row["bank_name"] or ""))
            if len(account_labels) != len(accounts):
                raise VaultError("Не найдены счета снимка экспорта.")
        category_labels: list[tuple[str, str]] = []
        if categories:
            values = {
                row["id"]: (row["name"], row["parent_id"])
                for row in connection.execute(
                    text("""SELECT e.id,c.name,c.parent_id FROM entities e
                JOIN category_revisions c ON c.revision_id=e.accepted_revision_id
                WHERE e.book_id=:book AND e.kind='category'"""),
                    {"book": self._book_id},
                ).mappings()
            }
            for identifier in sorted(categories):
                names, seen = [], set()
                current: str | None = identifier
                while current is not None:
                    if current in seen or current not in values:
                        raise VaultError("Нарушена иерархия категорий снимка экспорта.")
                    seen.add(current)
                    name, current = values[current]
                    names.append(name)
                category_labels.append((identifier, " / ".join(reversed(names))))
        tag_labels: list[tuple[str, str]] = []
        if tags:
            query = text("""SELECT e.id,t.name FROM entities e
                JOIN tag_revisions t ON t.revision_id=e.accepted_revision_id
                WHERE e.book_id=:book AND e.kind='tag' AND e.id IN :ids
                ORDER BY e.id""").bindparams(bindparam("ids", expanding=True))
            tag_labels = [
                (row["id"], row["name"])
                for row in connection.execute(
                    query, {"book": self._book_id, "ids": sorted(tags)}
                ).mappings()
            ]
            if len(tag_labels) != len(tags):
                raise VaultError("Не найдены теги снимка экспорта.")
        return ExportLabels(
            tuple(account_labels), tuple(bank_labels), tuple(category_labels), tuple(tag_labels)
        )

    def preview(self, settings: ExportSettings) -> ExportPreview:
        if not isinstance(settings, ExportSettings):
            raise ValidationError("Некорректный состав экспорта.")
        if settings.query.tag_ids and not self._ledger.supports_tags:
            raise ValidationError("Для тегов создайте копию и восстановите её в новую папку.")
        with self._mutex, self._engine.connect() as connection:
            operations = tuple(self._ledger._current_operations(connection))
            institutions = {
                row["id"]: row["institution_id"]
                for row in connection.execute(
                    text("""SELECT e.id,a.institution_id FROM entities e
                JOIN account_revisions a ON a.revision_id=e.accepted_revision_id
                WHERE e.book_id=:book AND e.kind='account'"""),
                    {"book": self._book_id},
                ).mappings()
            }
            preview = prepare_journal_export(operations, settings, institutions)
            labels = (
                self._read_labels(connection, preview.operations, settings.include_tags)
                if settings.include_names
                else ExportLabels()
            )
            return replace(preview, labels=labels, source_book_id=self._book_id)
