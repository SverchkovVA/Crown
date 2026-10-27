"""Атомарный импорт через общий путь финансовых версий, без частичных записей."""

import json
from collections import Counter
from datetime import date
from threading import RLock
from uuid import UUID, uuid4, uuid5

from sqlalchemy import Connection, Engine, text
from sqlalchemy.engine import RowMapping

from personal_finance.application.catalog import utc_now
from personal_finance.domain.errors import ValidationError, VaultError
from personal_finance.domain.imports import (
    MAX_IMPORT_ROWS,
    ImportDocument,
    ImportKind,
    ImportPreview,
    ImportPreviewLine,
    ImportResult,
    ImportRow,
    ImportSelection,
    ImportStatus,
    account_movement,
    digest_json,
    require_digest,
)
from personal_finance.domain.ledger import Operation, OperationKind, require_identifier
from personal_finance.domain.models import RUB, Currency, Money
from personal_finance.infrastructure.persistence.ledger_repository import (
    SqlLedgerRepository,
    operation_payload,
)
from personal_finance.infrastructure.security.keys import canonical_json


class SqlImportRepository:
    def __init__(
        self, engine: Engine, book_id: str, mutex: RLock, ledger: SqlLedgerRepository
    ) -> None:
        self._engine, self._book_id, self._mutex, self._ledger = engine, book_id, mutex, ledger

    def _account(self, connection: Connection, account_id: str, *, active: bool = True) -> None:
        require_identifier(account_id)
        found = connection.execute(
            text("""SELECT 1 FROM entities e JOIN entity_revisions r
            ON r.id=e.accepted_revision_id JOIN account_revisions a ON a.revision_id=r.id
            WHERE e.id=:id AND e.book_id=:book AND e.kind='account' AND r.sealed=1
            AND (:active=0 OR r.is_deleted=0) AND a.currency='RUB'"""),
            {"id": account_id, "book": self._book_id, "active": int(active)},
        ).first()
        if found is None:
            raise ValidationError("Выберите активный счёт RUB из этой книги.")

    def _preview(
        self, connection: Connection, document: ImportDocument, account_id: str
    ) -> ImportPreview:
        self._account(connection, account_id)
        if any(r.amount.currency != RUB for r in document.rows):
            raise ValidationError("Импорт поддерживает только RUB без конвертации.")
        known = {
            r["source_key"]: r["source_hash"]
            for r in connection.execute(
                text(
                    "SELECT source_key,source_hash FROM import_items "
                    "WHERE book_id=:book AND account_id=:account"
                ),
                {"book": self._book_id, "account": account_id},
            ).mappings()
        }
        candidates: dict[tuple[date, int], list[Operation]] = {}
        for operation in self._ledger._current_operations(connection):
            delta = account_movement(operation.draft, account_id)
            if not operation.is_deleted and operation.draft.kind != OperationKind.OPENING and delta:
                candidates.setdefault((operation.draft.occurred_on, delta), []).append(operation)
        identities: dict[str, set[str]] = {}
        for row in document.rows:
            identities.setdefault(document.source_key(row), set()).add(row.payload_hash)
        similar = Counter((r.occurred_on, r.amount.minor) for r in document.rows)
        seen: set[str] = set()
        lines = []
        for row in document.rows:
            key = document.source_key(row)
            options = tuple(candidates.get((row.occurred_on, row.amount.minor), ()))
            status, message = ImportStatus.NEW, "Новая строка"
            if len(identities[key]) > 1:
                status, message = (
                    ImportStatus.CONFLICT,
                    "Один внешний ID содержит разные сведения в этом файле.",
                )
            elif key in known:
                if known[key] == row.payload_hash:
                    status, message = (
                        ImportStatus.DUPLICATE,
                        "Эта строка уже импортирована или связана; повтор исключён.",
                    )
                else:
                    status, message = (
                        ImportStatus.CONFLICT,
                        "Изменились сведения уже известного ID. Нужна ручная проверка.",
                    )
            elif key in seen:
                status, message = (
                    ImportStatus.DUPLICATE,
                    "Повтор внешнего ID в файле; используйте первую строку.",
                )
            elif options or similar[(row.occurred_on, row.amount.minor)] > 1 or row.transfer_hint:
                status = ImportStatus.POSSIBLE
                message = "Возможный повтор: выберите связь или подтвердите отдельную операцию."
                if row.transfer_hint:
                    message = "Возможный перевод: уточните вид или выберите существующую операцию."
            seen.add(key)
            lines.append(ImportPreviewLine(row, status, options, message))
        return ImportPreview(document, account_id, tuple(lines))

    def preview(self, document: ImportDocument, account_id: str) -> ImportPreview:
        if not isinstance(document, ImportDocument):
            raise ValidationError("Некорректный документ импорта.")
        with self._mutex, self._engine.connect() as connection:
            return self._preview(connection, document, account_id)

    def _request(
        self, document: ImportDocument, account_id: str, selections: tuple[ImportSelection, ...]
    ) -> str:
        if (
            not isinstance(document, ImportDocument)
            or not isinstance(selections, tuple)
            or not 1 <= len(selections) <= MAX_IMPORT_ROWS
            or any(not isinstance(s, ImportSelection) for s in selections)
        ):
            raise ValidationError("Выберите от 1 до 2000 корректных строк импорта.")
        require_identifier(account_id)
        rows = {r.position: r for r in document.rows}
        if len({s.position for s in selections}) != len(selections) or any(
            s.position not in rows for s in selections
        ):
            raise ValidationError("Выбранные строки отсутствуют в документе или повторяются.")
        linked = [
            s.existing_operation_id for s in selections if s.existing_operation_id is not None
        ]
        if len(set(linked)) != len(linked):
            raise ValidationError(
                "Несколько разных строк нельзя связать с одной операцией в одном импорте."
            )
        return digest_json(
            {
                "kind": "statement-import-v1",
                "source_kind": document.kind.value,
                "file_hash": document.file_hash,
                "scope_hash": document.scope_hash,
                "account_id": account_id,
                "items": [
                    {
                        "position": s.position,
                        "source_key": document.source_key(rows[s.position]),
                        "source_hash": rows[s.position].payload_hash,
                        "draft": operation_payload(s.draft),
                        "acknowledged_duplicate": s.acknowledged_duplicate,
                        "existing_operation_id": s.existing_operation_id,
                        "expected_revision_id": s.expected_revision_id,
                    }
                    for s in sorted(selections, key=lambda s: s.position)
                ],
            }
        )

    def _result(self, connection: Connection, batch_id: str) -> ImportResult:
        rows = list(
            connection.execute(
                text("""SELECT i.revision_id,i.linked FROM import_items i
            JOIN import_batches b ON b.id=i.batch_id WHERE b.id=:id AND b.book_id=:book
            AND b.sealed=1 ORDER BY i.position"""),
                {"id": batch_id, "book": self._book_id},
            ).mappings()
        )
        if not rows:
            raise VaultError("Подтверждённая группа импорта не найдена.")
        return ImportResult(
            batch_id,
            tuple(self._ledger._revision(connection, r["revision_id"]) for r in rows),
            sum(r["linked"] for r in rows),
        )

    def _insert_item(
        self,
        connection: Connection,
        batch_id: str,
        account_id: str,
        document: ImportDocument,
        row: ImportRow,
        selection: ImportSelection,
        operation: Operation,
    ) -> None:
        connection.execute(
            text("""INSERT INTO import_items
            (batch_id,position,book_id,account_id,source_key,source_hash,source_json,
            operation_id,revision_id,linked,acknowledged_duplicate)
            VALUES (:batch,:position,:book,:account,:key,:hash,:source,
            :operation,:revision,:linked,:ack)"""),
            {
                "batch": batch_id,
                "position": row.position,
                "book": self._book_id,
                "account": account_id,
                "key": document.source_key(row),
                "hash": row.payload_hash,
                "source": canonical_json(row.as_dict()).decode(),
                "operation": operation.id,
                "revision": operation.revision_id,
                "linked": int(selection.existing_operation_id is not None),
                "ack": int(selection.acknowledged_duplicate),
            },
        )

    def confirm(
        self,
        document: ImportDocument,
        account_id: str,
        selections: tuple[ImportSelection, ...],
        command_id: str,
    ) -> ImportResult:
        digest = self._request(document, account_id, selections)
        require_identifier(command_id)
        selections = tuple(sorted(selections, key=lambda s: s.position))
        with self._mutex, self._engine.begin() as connection:
            previous = (
                connection.execute(
                    text(
                        "SELECT id,command_hash FROM import_batches "
                        "WHERE command_id=:id AND book_id=:book"
                    ),
                    {"id": command_id, "book": self._book_id},
                )
                .mappings()
                .first()
            )
            if previous is not None:
                if previous["command_hash"] != digest:
                    raise ValidationError(
                        "Идентификатор действия уже использован для других данных."
                    )
                return self._result(connection, previous["id"])
            if connection.execute(
                text("SELECT 1 FROM command_receipts WHERE id=:id"), {"id": command_id}
            ).first():
                raise ValidationError("Идентификатор действия уже использован для других данных.")
            preview = self._preview(connection, document, account_id)
            lines = {line.row.position: line for line in preview.lines}
            for selection in selections:
                line = lines[selection.position]
                if line.status in (ImportStatus.DUPLICATE, ImportStatus.CONFLICT):
                    raise ValidationError(
                        "Выбрана повторная или противоречивая строка. Обновите предпросмотр."
                    )
                if (
                    line.status == ImportStatus.POSSIBLE
                    and not selection.acknowledged_duplicate
                    and selection.existing_operation_id is None
                ):
                    raise ValidationError(
                        "Для возможного повтора подтвердите отдельную операцию или выберите связь."
                    )
                delta = account_movement(selection.draft, account_id)
                if (
                    selection.draft.amount.currency != line.row.amount.currency
                    or delta == 0
                    or (delta > 0) != (line.row.amount.minor > 0)
                ):
                    raise ValidationError(
                        "Строка должна сохранять валюту и направление движения выбранного счёта."
                    )
            batch_id, group_id = str(uuid4()), str(uuid4())
            created = utc_now()
            connection.execute(
                text(
                    "INSERT INTO change_groups(id,book_id,created_at) VALUES (:id,:book,:created)"
                ),
                {"id": group_id, "book": self._book_id, "created": created},
            )
            connection.execute(
                text("""INSERT INTO import_batches
                (id,book_id,account_id,kind,file_hash,scope_hash,command_id,
                command_hash,group_id,created_at,item_count)
                VALUES (:id,:book,:account,:kind,:file,:scope,:command,
                :hash,:group,:created,:count)"""),
                {
                    "id": batch_id,
                    "book": self._book_id,
                    "account": account_id,
                    "kind": document.kind.value,
                    "file": document.file_hash,
                    "scope": document.scope_hash,
                    "command": command_id,
                    "hash": digest,
                    "group": group_id,
                    "created": created,
                    "count": len(selections),
                },
            )
            operations = []
            for selection in selections:
                row = lines[selection.position].row
                if selection.existing_operation_id is not None:
                    operation = self._ledger._revision(
                        connection, selection.expected_revision_id or ""
                    )
                    accepted = connection.execute(
                        text(
                            "SELECT accepted_revision_id FROM entities "
                            "WHERE id=:id AND book_id=:book"
                        ),
                        {"id": selection.existing_operation_id, "book": self._book_id},
                    ).scalar_one_or_none()
                    if (
                        operation.id != selection.existing_operation_id
                        or accepted != operation.revision_id
                        or operation.is_deleted
                        or selection.draft != operation.draft
                    ):
                        raise ValidationError(
                            "Операция для связи изменилась. Обновите предпросмотр."
                        )
                    if (
                        operation.draft.occurred_on != row.occurred_on
                        or account_movement(operation.draft, account_id) != row.amount.minor
                    ):
                        raise ValidationError(
                            "Связь требует совпадения даты и суммы движения счёта с выпиской."
                        )
                else:
                    operation = self._ledger._save_in_transaction(
                        connection,
                        selection.draft,
                        command_id=str(uuid5(UUID(command_id), f"import/{selection.position}")),
                        group_id=group_id,
                        validate_state=False,
                    )
                self._insert_item(
                    connection, batch_id, account_id, document, row, selection, operation
                )
                operations.append(operation)
            self._ledger._validate_state(connection)
            connection.execute(
                text("UPDATE import_batches SET sealed=1 WHERE id=:id"), {"id": batch_id}
            )
            connection.execute(
                text(
                    "INSERT INTO command_receipts(id,command_hash,entity_id,revision_id) "
                    "VALUES (:id,:hash,:entity,:revision)"
                ),
                {
                    "id": command_id,
                    "hash": digest,
                    "entity": operations[0].id,
                    "revision": operations[0].revision_id,
                },
            )
            return self._result(connection, batch_id)

    def _stored_row(self, row: RowMapping) -> ImportRow:
        source = json.loads(row["source_json"])
        if not isinstance(source, dict) or set(source) != {
            "position",
            "occurred_on",
            "minor",
            "currency",
            "exponent",
            "note",
            "external_id",
            "transfer_hint",
        }:
            raise VaultError("Некорректный снимок строки импорта.")
        value = ImportRow(
            source["position"],
            date.fromisoformat(source["occurred_on"]),
            Money(source["minor"], Currency(source["currency"], source["exponent"])),
            source["note"],
            source["external_id"],
            source["transfer_hint"],
        )
        if (
            value.position != row["position"]
            or value.payload_hash != row["source_hash"]
            or canonical_json(value.as_dict()).decode() != row["source_json"]
        ):
            raise VaultError("Снимок строки импорта не соответствует отпечатку.")
        return value

    def check_integrity(self) -> None:
        with self._mutex, self._engine.connect() as connection:
            try:
                for batch in connection.execute(
                    text("SELECT * FROM import_batches WHERE book_id=:book"),
                    {"book": self._book_id},
                ).mappings():
                    require_identifier(batch["id"])
                    require_identifier(batch["command_id"])
                    for name in ("file_hash", "scope_hash", "command_hash"):
                        require_digest(batch[name])
                    self._account(connection, batch["account_id"], active=False)
                    items = list(
                        connection.execute(
                            text("SELECT * FROM import_items WHERE batch_id=:id ORDER BY position"),
                            {"id": batch["id"]},
                        ).mappings()
                    )
                    if batch["sealed"] != 1 or len(items) != batch["item_count"] or not items:
                        raise VaultError("Неполная группа импорта.")
                    rows, selections = [], []
                    for item in items:
                        source = self._stored_row(item)
                        operation = self._ledger._revision(connection, item["revision_id"])
                        if (
                            item["book_id"] != self._book_id
                            or item["account_id"] != batch["account_id"]
                            or item["operation_id"] != operation.id
                            or operation.is_deleted
                        ):
                            raise VaultError("Неверная связь строки импорта.")
                        delta = account_movement(operation.draft, batch["account_id"])
                        if (
                            not delta
                            or (delta > 0) != (source.amount.minor > 0)
                            or operation.draft.amount.currency != source.amount.currency
                        ):
                            raise VaultError("Неверное движение импортированной операции.")
                        group: str = connection.execute(
                            text("SELECT group_id FROM entity_revisions WHERE id=:id"),
                            {"id": operation.revision_id},
                        ).scalar_one()
                        if not item["linked"] and group != batch["group_id"]:
                            raise VaultError("Неверная атомарная группа импорта.")
                        if item["linked"] and (
                            operation.draft.occurred_on != source.occurred_on
                            or delta != source.amount.minor
                        ):
                            raise VaultError("Связанная операция не соответствует выписке.")
                        rows.append(source)
                        selections.append(
                            ImportSelection(
                                source.position,
                                operation.draft,
                                bool(item["acknowledged_duplicate"]),
                                operation.id if item["linked"] else None,
                                operation.revision_id if item["linked"] else None,
                            )
                        )
                    document = ImportDocument(
                        ImportKind(batch["kind"]),
                        batch["file_hash"],
                        batch["scope_hash"],
                        tuple(rows),
                    )
                    if (
                        any(
                            document.source_key(source) != item["source_key"]
                            for source, item in zip(rows, items, strict=True)
                        )
                        or self._request(document, batch["account_id"], tuple(selections))
                        != batch["command_hash"]
                    ):
                        raise VaultError("Содержимое группы импорта не соответствует отпечатку.")
                    receipt = (
                        connection.execute(
                            text(
                                "SELECT command_hash,entity_id,revision_id FROM command_receipts "
                                "WHERE id=:id"
                            ),
                            {"id": batch["command_id"]},
                        )
                        .mappings()
                        .first()
                    )
                    if (
                        receipt is None
                        or receipt["command_hash"] != batch["command_hash"]
                        or receipt["entity_id"] != items[0]["operation_id"]
                        or receipt["revision_id"] != items[0]["revision_id"]
                    ):
                        raise VaultError("Отсутствует квитанция группы импорта.")
            except (ValidationError, ValueError, TypeError, KeyError, AttributeError) as exc:
                raise VaultError("Проверка истории импорта не пройдена.") from exc
