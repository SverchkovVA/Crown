"""Атомарная история правил; правила не изменяют операции и проводки."""

import hashlib
from threading import RLock
from uuid import uuid4

from sqlalchemy import Connection, Engine, text
from sqlalchemy.engine import RowMapping

from personal_finance.application.catalog import utc_now
from personal_finance.domain.classification import (
    MAX_CATEGORY_RULES,
    CategoryRule,
    CategoryRuleVersion,
    CategorySuggestion,
    ClassificationInput,
    ClassificationSnapshot,
)
from personal_finance.domain.errors import ValidationError, VaultError
from personal_finance.domain.ledger import OperationKind, require_identifier
from personal_finance.domain.models import Category, CategoryKind
from personal_finance.infrastructure.security.keys import canonical_json

_SELECT = """SELECT e.id,r.id AS revision_id,r.sealed,r.is_deleted,r.created_at,r.content_hash,
    p.name,p.name_key,p.operation_kind,p.contains_text,p.category_id,p.priority,p.account_id
    FROM entities e JOIN entity_revisions r ON r.entity_id=e.id
    LEFT JOIN category_rule_revisions p ON p.revision_id=r.id
    WHERE e.book_id=:book AND e.kind='category_rule'"""


def rule_payload(rule: CategoryRule) -> dict[str, object]:
    return {
        "name": rule.name,
        "kind": rule.kind.value,
        "contains": rule.contains,
        "category_id": rule.category_id,
        "priority": rule.priority,
        "account_id": rule.account_id,
    }


def rule_hash(rule: CategoryRule, parent: str | None) -> str:
    return hashlib.sha256(
        canonical_json(
            {
                "kind": "category_rule",
                "payload": rule_payload(rule),
                "is_deleted": not rule.enabled,
                "parent_revision_id": parent,
            }
        )
    ).hexdigest()


class SqlClassificationRepository:
    def __init__(self, engine: Engine, book_id: str, replica_id: str, mutex: RLock) -> None:
        self._engine, self._book_id, self._replica_id, self._mutex = (
            engine,
            book_id,
            replica_id,
            mutex,
        )

    def _record(self, row: RowMapping) -> CategoryRuleVersion:
        require_identifier(row["id"])
        require_identifier(row["revision_id"])
        if row["sealed"] != 1 or row["is_deleted"] not in (0, 1):
            raise VaultError("Неверное состояние версии правила категории.")
        rule = CategoryRule(
            row["id"],
            row["name"],
            OperationKind(row["operation_kind"]),
            row["contains_text"],
            row["category_id"],
            row["priority"],
            row["account_id"],
            not bool(row["is_deleted"]),
        )
        if (
            rule.name != row["name"]
            or rule.name.casefold() != row["name_key"]
            or rule.contains != row["contains_text"]
        ):
            raise VaultError("Неканоническое содержимое правила категории.")
        return CategoryRuleVersion(rule, row["revision_id"], row["created_at"])

    def _revision(self, connection: Connection, revision: str) -> CategoryRuleVersion:
        row = (
            connection.execute(
                text(_SELECT + " AND r.id=:revision"), {"book": self._book_id, "revision": revision}
            )
            .mappings()
            .first()
        )
        if row is None:
            raise ValidationError("Версия правила не найдена в этой книге.")
        return self._record(row)

    def _current(self, connection: Connection, identifier: str) -> CategoryRuleVersion:
        row = (
            connection.execute(
                text(_SELECT + " AND e.id=:id AND r.id=e.accepted_revision_id"),
                {"book": self._book_id, "id": identifier},
            )
            .mappings()
            .first()
        )
        if row is None:
            raise ValidationError("Правило не найдено в этой книге.")
        return self._record(row)

    def _rules(self, connection: Connection, include_disabled: bool) -> list[CategoryRuleVersion]:
        return [
            self._record(row)
            for row in connection.execute(
                text(
                    _SELECT
                    + " AND r.id=e.accepted_revision_id AND (:disabled=1 OR r.is_deleted=0) "
                    "ORDER BY p.priority DESC,p.name_key,e.id"
                ),
                {"book": self._book_id, "disabled": int(include_disabled)},
            ).mappings()
        ]

    def rules(self, *, include_disabled: bool = False) -> list[CategoryRuleVersion]:
        if type(include_disabled) is not bool:
            raise ValidationError("Некорректный признак выключенных правил.")
        with self._mutex, self._engine.connect() as connection:
            return self._rules(connection, include_disabled)

    def _require_references(self, connection: Connection, rule: CategoryRule) -> None:
        category = connection.execute(
            text("""SELECT c.kind FROM entities e
            JOIN entity_revisions r ON r.id=e.accepted_revision_id
            JOIN category_revisions c ON c.revision_id=r.id
            WHERE e.id=:id AND e.book_id=:book AND e.kind='category' AND r.sealed=1"""),
            {"book": self._book_id, "id": rule.category_id},
        ).scalar_one_or_none()
        if category != rule.kind.value:
            raise ValidationError("Выберите категорию соответствующего вида в этой книге.")
        if (
            rule.account_id is not None
            and connection.execute(
                text("""SELECT 1 FROM entities e
            JOIN entity_revisions r ON r.id=e.accepted_revision_id WHERE e.id=:id
            AND e.book_id=:book AND e.kind='account' AND r.sealed=1"""),
                {"book": self._book_id, "id": rule.account_id},
            ).first()
            is None
        ):
            raise ValidationError("Счёт правила не найден в этой книге.")

    def _history(self, connection: Connection, identifier: str) -> list[CategoryRuleVersion]:
        rows = list(
            connection.execute(
                text(_SELECT + " AND e.id=:id"), {"book": self._book_id, "id": identifier}
            ).mappings()
        )
        records = {row["revision_id"]: self._record(row) for row in rows}
        hashes = {row["revision_id"]: row["content_hash"] for row in rows}
        if not records:
            raise ValidationError("История правила не найдена в этой книге.")
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
                raise VaultError("Нарушена последовательность версий правила.")
            visited.add(current)
            value = records[current]
            chain.append(value)
            parent = parents[current][0] if parents[current] else None
            if hashes[current] != rule_hash(value.rule, parent):
                raise VaultError("Содержимое правила не соответствует хешу.")
            self._require_references(connection, value.rule)
            current = parent
        chain.reverse()
        if len(visited) != len(records) or not chain[0].rule.enabled:
            raise VaultError("Непринятые ветви истории правила.")
        heads: list[str] = list(
            connection.execute(
                text("SELECT revision_id FROM revision_heads WHERE entity_id=:id"),
                {"id": identifier},
            ).scalars()
        )
        if heads != [chain[-1].revision_id]:
            raise VaultError("Неверная принятая ветвь правила.")
        return chain

    def history(self, identifier: str) -> list[CategoryRuleVersion]:
        require_identifier(identifier)
        with self._mutex, self._engine.connect() as connection:
            return self._history(connection, identifier)

    def save(
        self,
        rule: CategoryRule,
        *,
        expected_revision_id: str | None = None,
        command_id: str | None = None,
    ) -> CategoryRuleVersion:
        if not isinstance(rule, CategoryRule):
            raise ValidationError("Некорректное правило категории.")
        if expected_revision_id is not None:
            require_identifier(expected_revision_id)
        command_id = command_id or str(uuid4())
        require_identifier(command_id)
        digest = hashlib.sha256(
            canonical_json(
                {
                    "kind": "category_rule-command",
                    "id": rule.id,
                    "payload": rule_payload(rule),
                    "is_deleted": not rule.enabled,
                    "expected_revision_id": expected_revision_id,
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
                return self._revision(connection, receipt["revision_id"])
            if expected_revision_id is None:
                if not rule.enabled:
                    raise ValidationError("Новое правило должно быть включено.")
                if connection.execute(
                    text("SELECT 1 FROM entities WHERE id=:id"), {"id": rule.id}
                ).first():
                    raise ValidationError(
                        "Идентификатор записи уже существует. Обновите список правил."
                    )
            elif self._current(connection, rule.id).revision_id != expected_revision_id:
                raise ValidationError("Правило изменено. Обновите список и повторите действие.")
            self._require_references(connection, rule)
            current = self._rules(connection, False)
            if rule.enabled:
                if any(
                    v.rule.id != rule.id and v.rule.name.casefold() == rule.name.casefold()
                    for v in current
                ):
                    raise ValidationError("Включённое правило с таким названием уже существует.")
                if sum(v.rule.id != rule.id for v in current) >= MAX_CATEGORY_RULES:
                    raise ValidationError("Допускается не более 1000 включённых правил.")
            if expected_revision_id is None:
                connection.execute(
                    text(
                        "INSERT INTO entities(id,book_id,kind) VALUES (:id,:book,'category_rule')"
                    ),
                    {"id": rule.id, "book": self._book_id},
                )
            revision, group = str(uuid4()), str(uuid4())
            created = utc_now()
            sequence: int = connection.execute(
                text(
                    "UPDATE replicas SET sequence=sequence+1 WHERE id=:id "
                    "AND book_id=:book RETURNING sequence"
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
                    "e": rule.id,
                    "replica": self._replica_id,
                    "sequence": sequence,
                    "g": group,
                    "created": created,
                    "hash": rule_hash(rule, expected_revision_id),
                    "deleted": int(not rule.enabled),
                },
            )
            self._insert_payload(connection, revision, rule)
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
                {"r": revision, "id": rule.id},
            )
            connection.execute(
                text("DELETE FROM revision_heads WHERE entity_id=:id"), {"id": rule.id}
            )
            connection.execute(
                text("INSERT INTO revision_heads(entity_id,revision_id) VALUES (:e,:r)"),
                {"e": rule.id, "r": revision},
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
                {"id": command_id, "hash": digest, "e": rule.id, "r": revision},
            )
            return self._revision(connection, revision)

    def _insert_payload(self, connection: Connection, revision: str, rule: CategoryRule) -> None:
        connection.execute(
            text("""INSERT INTO category_rule_revisions
            (revision_id,name,name_key,operation_kind,contains_text,category_id,priority,account_id)
            VALUES (:r,:n,:key,:kind,:contains,:category,:priority,:account)"""),
            {
                "r": revision,
                "n": rule.name,
                "key": rule.name.casefold(),
                "kind": rule.kind.value,
                "contains": rule.contains,
                "category": rule.category_id,
                "priority": rule.priority,
                "account": rule.account_id,
            },
        )

    def snapshot(self) -> ClassificationSnapshot:
        with self._mutex, self._engine.connect() as connection:
            rules = tuple(v.rule for v in self._rules(connection, False))
            categories = tuple(
                Category(
                    row["id"],
                    row["name"],
                    CategoryKind(row["kind"]),
                    row["parent_id"],
                    row["revision_id"],
                )
                for row in connection.execute(
                    text("""SELECT e.id,c.name,c.kind,c.parent_id,c.revision_id FROM entities e
                    JOIN category_revisions c ON c.revision_id=e.accepted_revision_id
                    WHERE e.book_id=:book AND e.kind='category'"""),
                    {"book": self._book_id},
                ).mappings()
            )
            return ClassificationSnapshot(rules, categories)

    def suggest(self, value: ClassificationInput) -> CategorySuggestion:
        return self.snapshot().suggest(value)

    def check_integrity(self) -> None:
        with self._mutex, self._engine.connect() as connection:
            try:
                for value in self._rules(connection, True):
                    self._history(connection, value.rule.id)
            except (ValidationError, ValueError, TypeError, KeyError, AttributeError) as exc:
                raise VaultError("Проверка правил категоризации не пройдена.") from exc
