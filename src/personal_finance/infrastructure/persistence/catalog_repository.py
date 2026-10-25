"""SQL-адаптер справочников и атомарной истории изменений."""

import hashlib
from collections.abc import Mapping
from threading import RLock
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import Engine, text

from personal_finance.application.catalog import utc_now
from personal_finance.domain.errors import ValidationError, VaultError
from personal_finance.domain.models import Account, AccountKind, Category, CategoryKind, Institution
from personal_finance.infrastructure.security.keys import canonical_json


class SqlCatalogRepository:
    def __init__(self, engine: Engine, book_id: str, replica_id: str, mutex: RLock) -> None:
        self._engine = engine
        self._book_id = book_id
        self._replica_id = replica_id
        self._mutex = mutex

    def create_revision(
        self, kind: str, payload: Mapping[str, Any], command_id: str | None
    ) -> tuple[str, str]:
        command_id = command_id or str(uuid4())
        try:
            if str(UUID(command_id)) != command_id:
                raise ValueError
        except ValueError as exc:
            raise ValidationError("Недопустимый идентификатор действия.") from exc
        digest = hashlib.sha256(canonical_json({"kind": kind, "payload": payload})).hexdigest()
        with self._mutex, self._engine.begin() as connection:
            receipt = (
                connection.execute(
                    text(
                        "SELECT command_hash, entity_id, revision_id "
                        "FROM command_receipts WHERE id=:id"
                    ),
                    {"id": command_id},
                )
                .mappings()
                .first()
            )
            if receipt:
                if receipt["command_hash"] != digest:
                    raise ValidationError(
                        "Идентификатор действия уже использован для других данных."
                    )
                return receipt["entity_id"], receipt["revision_id"]
            if kind == "account" and payload["institution_id"] is not None:
                valid = connection.execute(
                    text("""SELECT 1 FROM entities e
                    JOIN entity_revisions r ON r.id=e.accepted_revision_id
                    WHERE e.id=:id AND e.book_id=:book AND e.kind='institution'
                    AND r.is_deleted=0"""),
                    {"id": payload["institution_id"], "book": self._book_id},
                ).first()
                if not valid:
                    raise ValidationError("Выбранный банк не найден в этой книге.")
            if kind == "category" and payload["parent_id"] is not None:
                parent = connection.execute(
                    text("""SELECT c.kind FROM entities e
                    JOIN entity_revisions r ON r.id=e.accepted_revision_id
                    JOIN category_revisions c ON c.revision_id=r.id
                    WHERE e.id=:id AND e.book_id=:book AND e.kind='category'
                    AND r.is_deleted=0"""),
                    {"id": payload["parent_id"], "book": self._book_id},
                ).first()
                if not parent or parent[0] != payload["kind"]:
                    raise ValidationError("Родительская категория должна иметь тот же тип.")
            sequence: int = connection.execute(
                text("""UPDATE replicas SET sequence=sequence+1
                WHERE id=:id AND book_id=:book RETURNING sequence"""),
                {"id": self._replica_id, "book": self._book_id},
            ).scalar_one()
            entity_id, revision_id, group_id = (str(uuid4()) for _ in range(3))
            connection.execute(
                text("""INSERT INTO entities (id,book_id,kind)
                VALUES (:id,:book,:kind)"""),
                {"id": entity_id, "book": self._book_id, "kind": kind},
            )
            connection.execute(
                text("""INSERT INTO change_groups (id,book_id,created_at)
                VALUES (:id,:book,:created)"""),
                {"id": group_id, "book": self._book_id, "created": utc_now()},
            )
            connection.execute(
                text("""INSERT INTO entity_revisions
                (id,entity_id,replica_id,sequence,group_id,created_at,content_hash)
                VALUES (:id,:entity,:replica,:sequence,:group_id,:created,:digest)"""),
                {
                    "id": revision_id,
                    "entity": entity_id,
                    "replica": self._replica_id,
                    "sequence": sequence,
                    "group_id": group_id,
                    "created": utc_now(),
                    "digest": digest,
                },
            )
            # Имена таблиц и колонок выбираются только из внутренних констант.
            tables = {
                "institution": ("institution_revisions", ("name",)),
                "account": ("account_revisions", ("name", "kind", "institution_id", "currency")),
                "category": ("category_revisions", ("name", "kind", "parent_id")),
            }
            table, columns = tables[kind]
            names = ",".join(("revision_id", *columns))
            placeholders = ",".join(":" + name for name in ("revision_id", *columns))
            connection.execute(
                text(f"INSERT INTO {table} ({names}) VALUES ({placeholders})"),
                {"revision_id": revision_id, **payload},
            )
            connection.execute(
                text("UPDATE entity_revisions SET sealed=1 WHERE id=:id"), {"id": revision_id}
            )
            connection.execute(
                text("UPDATE entities SET accepted_revision_id=:revision WHERE id=:id"),
                {"revision": revision_id, "id": entity_id},
            )
            connection.execute(
                text("INSERT INTO revision_heads (entity_id,revision_id) VALUES (:e,:r)"),
                {"e": entity_id, "r": revision_id},
            )
            connection.execute(
                text("""INSERT INTO change_log
                (id,replica_id,sequence,group_id,revision_id) VALUES (:id,:replica,:seq,:g,:r)"""),
                {
                    "id": str(uuid4()),
                    "replica": self._replica_id,
                    "seq": sequence,
                    "g": group_id,
                    "r": revision_id,
                },
            )
            connection.execute(
                text("""INSERT INTO command_receipts
                (id,command_hash,entity_id,revision_id) VALUES (:id,:h,:e,:r)"""),
                {"id": command_id, "h": digest, "e": entity_id, "r": revision_id},
            )
            return entity_id, revision_id

    def _rows(self, table: str, columns: str) -> list[dict[str, Any]]:
        with self._mutex, self._engine.connect() as connection:
            return [
                dict(row)
                for row in connection.execute(
                    text(f"""SELECT e.id, r.id AS revision_id, {columns}
                FROM entities e JOIN entity_revisions r ON r.id=e.accepted_revision_id
                JOIN {table} p ON p.revision_id=r.id
                WHERE e.book_id=:book AND r.is_deleted=0
                ORDER BY p.name COLLATE NOCASE, e.id"""),
                    {"book": self._book_id},
                ).mappings()
            ]

    def institutions(self) -> list[Institution]:
        return [
            Institution(r["id"], r["name"]) for r in self._rows("institution_revisions", "p.name")
        ]

    def accounts(self) -> list[Account]:
        return [
            Account(
                r["id"],
                r["name"],
                AccountKind(r["kind"]),
                r["institution_id"],
                r["currency"],
                r["revision_id"],
            )
            for r in self._rows("account_revisions", "p.name,p.kind,p.institution_id,p.currency")
        ]

    def categories(self) -> list[Category]:
        return [
            Category(r["id"], r["name"], CategoryKind(r["kind"]), r["parent_id"], r["revision_id"])
            for r in self._rows("category_revisions", "p.name,p.kind,p.parent_id")
        ]

    def check_integrity(self) -> None:
        tables = {
            "institution": ("institution_revisions", ("name",)),
            "account": ("account_revisions", ("name", "kind", "institution_id", "currency")),
            "category": ("category_revisions", ("name", "kind", "parent_id")),
        }
        with self._mutex, self._engine.connect() as connection:
            for kind, (table, columns) in tables.items():
                names = ",".join(f"p.{column}" for column in columns)
                rows = connection.execute(
                    text(f"""SELECT r.content_hash, {names}
                    FROM entity_revisions r JOIN entities e ON e.id=r.entity_id
                    LEFT JOIN {table} p ON p.revision_id=r.id WHERE e.book_id=:book
                    AND e.kind=:kind"""),
                    {"book": self._book_id, "kind": kind},
                ).mappings()
                for row in rows:
                    payload = {column: row[column] for column in columns}
                    digest = hashlib.sha256(
                        canonical_json({"kind": kind, "payload": payload})
                    ).hexdigest()
                    if digest != row["content_hash"]:
                        raise VaultError("Содержимое версии не соответствует контрольному хешу.")
