"""Атомарные версии тегов и сохранённых фильтров; только константные имена SQL."""

import hashlib
import json
from threading import RLock
from typing import Any
from uuid import uuid4

from sqlalchemy import Connection, Engine, text
from sqlalchemy.engine import RowMapping

from personal_finance.application.catalog import utc_now
from personal_finance.domain.errors import ValidationError, VaultError
from personal_finance.domain.ledger import require_identifier
from personal_finance.domain.models import validate_name
from personal_finance.domain.organization import SavedFilter, Tag
from personal_finance.domain.query import OperationQuery
from personal_finance.infrastructure.security.keys import canonical_json

_TABLES = {"tag": "tag_revisions", "saved_filter": "saved_filter_revisions"}


def definition_payload(name: str, query: OperationQuery | None) -> dict[str, Any]:
    result: dict[str, Any] = {"name": name}
    if query is not None:
        result["query"] = query.as_dict()
    return result


def definition_hash(
    kind: str, name: str, query: OperationQuery | None, deleted: bool, parent: str | None
) -> str:
    return hashlib.sha256(
        canonical_json(
            {
                "kind": kind,
                "payload": definition_payload(name, query),
                "is_deleted": deleted,
                "parent_revision_id": parent,
            }
        )
    ).hexdigest()


class SqlOrganizationRepository:
    def __init__(self, engine: Engine, book_id: str, replica_id: str, mutex: RLock) -> None:
        self._engine, self._book_id, self._replica_id, self._mutex = (
            engine,
            book_id,
            replica_id,
            mutex,
        )

    def _select(self, kind: str) -> str:
        if kind not in _TABLES:
            raise ValidationError("Неизвестный тип справочника организации.")
        query = "p.query_json" if kind == "saved_filter" else "NULL AS query_json"
        return f"""SELECT e.id,r.id AS revision_id,r.sealed,r.is_deleted,
            r.created_at,r.content_hash,
            p.name,p.name_key,{query} FROM entities e JOIN entity_revisions r ON r.entity_id=e.id
            LEFT JOIN {_TABLES[kind]} p ON p.revision_id=r.id
            WHERE e.book_id=:book AND e.kind=:kind"""

    def _record(self, kind: str, row: RowMapping) -> Tag | SavedFilter:
        require_identifier(row["id"])
        require_identifier(row["revision_id"])
        name = validate_name(row["name"])
        if (
            row["sealed"] != 1
            or row["is_deleted"] not in (0, 1)
            or name != row["name"]
            or name.casefold() != row["name_key"]
        ):
            raise VaultError("Неверное состояние версии тега или фильтра.")
        if kind == "tag":
            return Tag(
                row["id"], row["revision_id"], name, bool(row["is_deleted"]), row["created_at"]
            )
        value = row["query_json"]
        if not isinstance(value, str) or len(value) > 65536:
            raise VaultError("Некорректный размер фильтра.")
        query = OperationQuery.from_dict(json.loads(value))
        if canonical_json(query.as_dict()).decode() != value:
            raise VaultError("Неканоническое содержимое фильтра.")
        return SavedFilter(
            row["id"], row["revision_id"], name, query, bool(row["is_deleted"]), row["created_at"]
        )

    def _revision(self, connection: Connection, kind: str, revision: str) -> Tag | SavedFilter:
        row = (
            connection.execute(
                text(self._select(kind) + " AND r.id=:revision"),
                {"book": self._book_id, "kind": kind, "revision": revision},
            )
            .mappings()
            .first()
        )
        if row is None:
            raise ValidationError("Версия тега или фильтра не найдена в этой книге.")
        return self._record(kind, row)

    def _current(self, connection: Connection, kind: str, identifier: str) -> Tag | SavedFilter:
        row = (
            connection.execute(
                text(self._select(kind) + " AND e.id=:id AND r.id=e.accepted_revision_id"),
                {"book": self._book_id, "kind": kind, "id": identifier},
            )
            .mappings()
            .first()
        )
        if row is None:
            raise ValidationError("Тег или фильтр не найден в этой книге.")
        return self._record(kind, row)

    def _items(self, connection: Connection, kind: str, archived: bool) -> list[Tag | SavedFilter]:
        rows = connection.execute(
            text(
                self._select(kind)
                + " AND r.id=e.accepted_revision_id AND (:archived=1 OR r.is_deleted=0) "
                "ORDER BY p.name_key,e.id"
            ),
            {"book": self._book_id, "kind": kind, "archived": int(archived)},
        ).mappings()
        return [self._record(kind, row) for row in rows]

    def tags(self, *, include_archived: bool = False) -> list[Tag]:
        with self._mutex, self._engine.connect() as connection:
            values = self._items(connection, "tag", include_archived)
            return [value for value in values if isinstance(value, Tag)]

    def filters(self, *, include_archived: bool = False) -> list[SavedFilter]:
        with self._mutex, self._engine.connect() as connection:
            values = self._items(connection, "saved_filter", include_archived)
            return [value for value in values if isinstance(value, SavedFilter)]

    def tag(self, identifier: str) -> Tag:
        require_identifier(identifier)
        with self._mutex, self._engine.connect() as connection:
            value = self._current(connection, "tag", identifier)
            assert isinstance(value, Tag)
            return value

    def saved_filter(self, identifier: str) -> SavedFilter:
        require_identifier(identifier)
        with self._mutex, self._engine.connect() as connection:
            value = self._current(connection, "saved_filter", identifier)
            assert isinstance(value, SavedFilter)
            return value

    def _require_query_references(self, connection: Connection, query: OperationQuery) -> None:
        for kind, ids in (
            ("account", query.account_ids),
            ("category", query.category_ids),
            ("institution", query.institution_ids),
            ("tag", query.tag_ids),
        ):
            for identifier in ids:
                found = connection.execute(
                    text("""SELECT 1 FROM entities e JOIN entity_revisions r
                    ON r.id=e.accepted_revision_id WHERE e.id=:id AND e.book_id=:book
                    AND e.kind=:kind AND r.sealed=1"""),
                    {"id": identifier, "book": self._book_id, "kind": kind},
                ).first()
                if found is None:
                    raise ValidationError("Значение фильтра не найдено в этой книге.")

    def _history(
        self, connection: Connection, kind: str, identifier: str
    ) -> list[Tag | SavedFilter]:
        rows = list(
            connection.execute(
                text(self._select(kind) + " AND e.id=:id"),
                {"book": self._book_id, "kind": kind, "id": identifier},
            ).mappings()
        )
        records = {row["revision_id"]: self._record(kind, row) for row in rows}
        hashes = {row["revision_id"]: row["content_hash"] for row in rows}
        if not records:
            raise ValidationError("История тега или фильтра не найдена в этой книге.")
        parents: dict[str, list[str]] = {revision: [] for revision in records}
        for row in connection.execute(
            text("""SELECT p.revision_id,p.parent_id FROM revision_parents p
            JOIN entity_revisions r ON r.id=p.revision_id WHERE r.entity_id=:id"""),
            {"id": identifier},
        ).mappings():
            parents[row["revision_id"]].append(row["parent_id"])
        current: str | None = connection.execute(
            text("SELECT accepted_revision_id FROM entities WHERE id=:id"), {"id": identifier}
        ).scalar_one()
        chain = []
        visited = set()
        while current is not None:
            if current not in records or current in visited or len(parents[current]) > 1:
                raise VaultError("Нарушена последовательность версий тега или фильтра.")
            visited.add(current)
            value = records[current]
            chain.append(value)
            parent = parents[current][0] if parents[current] else None
            query = value.query if isinstance(value, SavedFilter) else None
            if hashes[current] != definition_hash(
                kind, value.name, query, value.is_deleted, parent
            ):
                raise VaultError("Содержимое тега или фильтра не соответствует хешу.")
            if query is not None:
                self._require_query_references(connection, query)
            current = parent
        chain.reverse()
        if len(visited) != len(records) or chain[0].is_deleted:
            raise VaultError("Непринятые ветви истории тега или фильтра.")
        heads: list[str] = list(
            connection.execute(
                text("SELECT revision_id FROM revision_heads WHERE entity_id=:id"),
                {"id": identifier},
            ).scalars()
        )
        if heads != [chain[-1].revision_id]:
            raise VaultError("Неверная принятая ветвь тега или фильтра.")
        return chain

    def history(self, kind: str, identifier: str) -> list[Tag | SavedFilter]:
        require_identifier(identifier)
        with self._mutex, self._engine.connect() as connection:
            return self._history(connection, kind, identifier)

    def _insert_payload(
        self,
        connection: Connection,
        kind: str,
        revision: str,
        name: str,
        query: OperationQuery | None,
    ) -> None:
        if kind == "tag":
            connection.execute(
                text("INSERT INTO tag_revisions(revision_id,name,name_key) VALUES (:r,:n,:k)"),
                {"r": revision, "n": name, "k": name.casefold()},
            )
        else:
            assert query is not None
            connection.execute(
                text(
                    "INSERT INTO saved_filter_revisions(revision_id,name,name_key,query_json) "
                    "VALUES (:r,:n,:k,:q)"
                ),
                {
                    "r": revision,
                    "n": name,
                    "k": name.casefold(),
                    "q": canonical_json(query.as_dict()).decode(),
                },
            )

    def _save(
        self,
        kind: str,
        name: str,
        query: OperationQuery | None,
        *,
        identifier: str | None,
        expected_revision_id: str | None,
        is_deleted: bool,
        command_id: str | None,
    ) -> Tag | SavedFilter:
        name = validate_name(name)
        if (
            kind not in _TABLES
            or type(is_deleted) is not bool
            or (kind == "saved_filter" and not isinstance(query, OperationQuery))
            or (kind == "tag" and query is not None)
        ):
            raise ValidationError("Некорректные данные тега или фильтра.")
        command_id = command_id or str(uuid4())
        require_identifier(command_id)
        if (identifier is None) != (expected_revision_id is None):
            raise ValidationError("Для изменения укажите ожидаемую версию тега или фильтра.")
        if identifier is not None:
            require_identifier(identifier)
        if expected_revision_id is not None:
            require_identifier(expected_revision_id)
        digest = hashlib.sha256(
            canonical_json(
                {
                    "kind": kind + "-command",
                    "payload": definition_payload(name, query),
                    "identifier": identifier,
                    "expected_revision_id": expected_revision_id,
                    "is_deleted": is_deleted,
                }
            )
        ).hexdigest()
        with self._mutex, self._engine.begin() as connection:
            receipt = (
                connection.execute(
                    text("SELECT command_hash,revision_id FROM command_receipts WHERE id=:id"),
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
                return self._revision(connection, kind, receipt["revision_id"])
            if identifier is not None:
                current = self._current(connection, kind, identifier)
                if current.revision_id != expected_revision_id:
                    raise ValidationError(
                        "Тег или фильтр изменён. Обновите список и повторите действие."
                    )
            elif is_deleted:
                raise ValidationError("Нельзя архивировать ещё не созданную запись.")
            if query is not None:
                self._require_query_references(connection, query)
            if not is_deleted and any(
                value.id != identifier and value.name.casefold() == name.casefold()
                for value in self._items(connection, kind, False)
            ):
                raise ValidationError("Активная запись с таким названием уже существует.")
            if identifier is None:
                identifier = str(uuid4())
                connection.execute(
                    text("INSERT INTO entities(id,book_id,kind) VALUES (:id,:book,:kind)"),
                    {"id": identifier, "book": self._book_id, "kind": kind},
                )
            revision, group = str(uuid4()), str(uuid4())
            created = utc_now()
            sequence: int = connection.execute(
                text(
                    "UPDATE replicas SET sequence=sequence+1 "
                    "WHERE id=:id AND book_id=:book RETURNING sequence"
                ),
                {"id": self._replica_id, "book": self._book_id},
            ).scalar_one()
            connection.execute(
                text(
                    "INSERT INTO change_groups(id,book_id,created_at) VALUES (:id,:book,:created)"
                ),
                {"id": group, "book": self._book_id, "created": created},
            )
            connection.execute(
                text("""INSERT INTO entity_revisions
                (id,entity_id,replica_id,sequence,group_id,created_at,content_hash,is_deleted)
                VALUES (:r,:e,:replica,:sequence,:g,:created,:hash,:deleted)"""),
                {
                    "r": revision,
                    "e": identifier,
                    "replica": self._replica_id,
                    "sequence": sequence,
                    "g": group,
                    "created": created,
                    "hash": definition_hash(kind, name, query, is_deleted, expected_revision_id),
                    "deleted": int(is_deleted),
                },
            )
            self._insert_payload(connection, kind, revision, name, query)
            if expected_revision_id:
                connection.execute(
                    text("INSERT INTO revision_parents(revision_id,parent_id) VALUES (:r,:p)"),
                    {"r": revision, "p": expected_revision_id},
                )
            connection.execute(
                text("UPDATE entity_revisions SET sealed=1 WHERE id=:id"), {"id": revision}
            )
            connection.execute(
                text("UPDATE entities SET accepted_revision_id=:r WHERE id=:id"),
                {"r": revision, "id": identifier},
            )
            connection.execute(
                text("DELETE FROM revision_heads WHERE entity_id=:id"), {"id": identifier}
            )
            connection.execute(
                text("INSERT INTO revision_heads(entity_id,revision_id) VALUES (:e,:r)"),
                {"e": identifier, "r": revision},
            )
            connection.execute(
                text(
                    "INSERT INTO change_log(id,replica_id,sequence,group_id,revision_id) "
                    "VALUES (:id,:replica,:seq,:g,:r)"
                ),
                {
                    "id": str(uuid4()),
                    "replica": self._replica_id,
                    "seq": sequence,
                    "g": group,
                    "r": revision,
                },
            )
            connection.execute(
                text(
                    "INSERT INTO command_receipts(id,command_hash,entity_id,revision_id) "
                    "VALUES (:id,:hash,:e,:r)"
                ),
                {"id": command_id, "hash": digest, "e": identifier, "r": revision},
            )
            return self._revision(connection, kind, revision)

    def save_tag(
        self,
        name: str,
        *,
        identifier: str | None = None,
        expected_revision_id: str | None = None,
        is_deleted: bool = False,
        command_id: str | None = None,
    ) -> Tag:
        value = self._save(
            "tag",
            name,
            None,
            identifier=identifier,
            expected_revision_id=expected_revision_id,
            is_deleted=is_deleted,
            command_id=command_id,
        )
        assert isinstance(value, Tag)
        return value

    def save_filter(
        self,
        name: str,
        query: OperationQuery,
        *,
        identifier: str | None = None,
        expected_revision_id: str | None = None,
        is_deleted: bool = False,
        command_id: str | None = None,
    ) -> SavedFilter:
        value = self._save(
            "saved_filter",
            name,
            query,
            identifier=identifier,
            expected_revision_id=expected_revision_id,
            is_deleted=is_deleted,
            command_id=command_id,
        )
        assert isinstance(value, SavedFilter)
        return value

    def check_integrity(self) -> None:
        with self._mutex, self._engine.connect() as connection:
            try:
                for kind in _TABLES:
                    for value in self._items(connection, kind, True):
                        self._history(connection, kind, value.id)
            except (ValidationError, ValueError, TypeError, KeyError, AttributeError) as exc:
                raise VaultError("Проверка тегов и сохранённых фильтров не пройдена.") from exc
