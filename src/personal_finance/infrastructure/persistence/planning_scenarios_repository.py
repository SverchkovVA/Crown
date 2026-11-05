"""Атомарные версии условий бюджета и цели и защита повторных команд."""

import hashlib
from threading import RLock
from typing import Any
from uuid import uuid4

from sqlalchemy import Connection, Engine, bindparam, text
from sqlalchemy.engine import RowMapping

from personal_finance.application.catalog import utc_now
from personal_finance.domain.errors import ValidationError, VaultError
from personal_finance.domain.ledger import require_identifier
from personal_finance.domain.models import validate_name
from personal_finance.domain.planning_scenarios import (
    BudgetScenario,
    PlanningKind,
    PlanningScenario,
    SavedPlanningScenario,
    planning_from_json,
    planning_json,
    planning_kind,
    planning_payload,
)
from personal_finance.infrastructure.security.keys import canonical_json

KIND = "planning_scenario"


def definition_payload(name: str, scenario: PlanningScenario) -> dict[str, Any]:
    return {"name": name, "scenario": planning_payload(scenario)}


def definition_hash(
    name: str, scenario: PlanningScenario, deleted: bool, parent: str | None
) -> str:
    return hashlib.sha256(
        canonical_json(
            {
                "kind": KIND,
                "payload": definition_payload(name, scenario),
                "is_deleted": deleted,
                "parent_revision_id": parent,
            }
        )
    ).hexdigest()


class SqlPlanningScenariosRepository:
    def __init__(self, engine: Engine, book_id: str, replica_id: str, mutex: RLock) -> None:
        self._engine, self._book_id, self._replica_id, self._mutex = (
            engine,
            book_id,
            replica_id,
            mutex,
        )

    def _select(self) -> str:
        return """SELECT e.id,r.id AS revision_id,r.sealed,r.is_deleted,r.created_at,r.content_hash,
            p.scenario_kind,p.name,p.name_key,p.scenario_json FROM entities e
            JOIN entity_revisions r ON r.entity_id=e.id
            LEFT JOIN planning_scenario_revisions p ON p.revision_id=r.id
            WHERE e.book_id=:book AND e.kind='planning_scenario'"""

    def _record(self, row: RowMapping) -> SavedPlanningScenario:
        require_identifier(row["id"])
        require_identifier(row["revision_id"])
        name = validate_name(row["name"])
        if (
            row["sealed"] != 1
            or row["is_deleted"] not in (0, 1)
            or name != row["name"]
            or name.casefold() != row["name_key"]
        ):
            raise VaultError("Неверное состояние версии условий планирования.")
        scenario = planning_from_json(row["scenario_json"])
        if row["scenario_kind"] != planning_kind(scenario).value:
            raise VaultError("Вид условий не соответствует сохранённому содержимому.")
        return SavedPlanningScenario(
            row["id"],
            row["revision_id"],
            name,
            scenario,
            bool(row["is_deleted"]),
            row["created_at"],
        )

    def _revision(self, connection: Connection, revision: str) -> SavedPlanningScenario:
        row = (
            connection.execute(
                text(self._select() + " AND r.id=:revision"),
                {"book": self._book_id, "revision": revision},
            )
            .mappings()
            .first()
        )
        if row is None:
            raise ValidationError("Версия условий планирования не найдена в этой книге.")
        return self._record(row)

    def _current(self, connection: Connection, identifier: str) -> SavedPlanningScenario:
        row = (
            connection.execute(
                text(self._select() + " AND e.id=:id AND r.id=e.accepted_revision_id"),
                {"book": self._book_id, "id": identifier},
            )
            .mappings()
            .first()
        )
        if row is None:
            raise ValidationError("Условия планирования не найдены в этой книге.")
        return self._record(row)

    def _items(
        self, connection: Connection, archived: bool, kind: PlanningKind | None = None
    ) -> list[SavedPlanningScenario]:
        rows = connection.execute(
            text(
                self._select()
                + " AND r.id=e.accepted_revision_id AND (:archived=1 OR r.is_deleted=0) "
                "AND (:kind IS NULL OR p.scenario_kind=:kind) ORDER BY p.scenario_kind,p.name_key,e.id"
            ),
            {
                "book": self._book_id,
                "archived": int(archived),
                "kind": None if kind is None else kind.value,
            },
        ).mappings()
        return [self._record(row) for row in rows]

    def items(
        self, *, kind: PlanningKind | None = None, include_archived: bool = False
    ) -> list[SavedPlanningScenario]:
        if type(include_archived) is not bool or (
            kind is not None and not isinstance(kind, PlanningKind)
        ):
            raise ValidationError("Некорректный выбор архивных сценариев.")
        with self._mutex, self._engine.connect() as connection:
            return self._items(connection, include_archived, kind)

    def get(self, identifier: str) -> SavedPlanningScenario:
        require_identifier(identifier)
        with self._mutex, self._engine.connect() as connection:
            return self._current(connection, identifier)

    def _history(self, connection: Connection, identifier: str) -> list[SavedPlanningScenario]:
        rows = list(
            connection.execute(
                text(self._select() + " AND e.id=:id"),
                {"book": self._book_id, "id": identifier},
            ).mappings()
        )
        records = {row["revision_id"]: self._record(row) for row in rows}
        hashes = {row["revision_id"]: row["content_hash"] for row in rows}
        if not records:
            raise ValidationError("История условий планирования не найдена в этой книге.")
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
                raise VaultError("Нарушена последовательность версий условий планирования.")
            visited.add(current)
            value = records[current]
            chain.append(value)
            parent = parents[current][0] if parents[current] else None
            if hashes[current] != definition_hash(
                value.name, value.scenario, value.is_deleted, parent
            ):
                raise VaultError("Содержимое условий планирования не соответствует хешу.")
            current = parent
        chain.reverse()
        if len({value.kind for value in chain}) != 1:
            raise VaultError("Вид условий не может меняться между версиями.")
        for value in chain:
            self._scope(connection, value.scenario, current=False)
        if not chain or len(visited) != len(records) or chain[0].is_deleted:
            raise VaultError("Непринятые ветви истории условий планирования.")
        heads: list[str] = list(
            connection.execute(
                text("SELECT revision_id FROM revision_heads WHERE entity_id=:id"),
                {"id": identifier},
            ).scalars()
        )
        if heads != [chain[-1].revision_id]:
            raise VaultError("Неверная принятая ветвь условий планирования.")
        return chain

    def history(self, identifier: str) -> list[SavedPlanningScenario]:
        require_identifier(identifier)
        with self._mutex, self._engine.connect() as connection:
            return self._history(connection, identifier)

    def _scope(self, connection: Connection, scenario: PlanningScenario, *, current: bool) -> None:
        if not isinstance(scenario, BudgetScenario):
            return
        references = (
            (
                scenario.scope.account_ids,
                "account",
                "account_revisions",
                " AND p.currency=:currency",
            ),
            (
                scenario.scope.category_ids,
                "category",
                "category_revisions",
                " AND p.kind='expense'",
            ),
        )
        for identifiers, kind, table, condition in references:
            if identifiers is None or not identifiers:
                continue
            ordered = sorted(identifiers)
            for offset in range(0, len(ordered), 500):
                chunk = ordered[offset : offset + 500]
                join = ""
                predicate = ""
                if current:
                    join = (
                        " JOIN entity_revisions r ON r.id=e.accepted_revision_id "
                        f"JOIN {table} p ON p.revision_id=r.id "
                    )
                    predicate = " AND r.sealed=1 AND r.is_deleted=0" + condition
                statement = text(
                    "SELECT e.id FROM entities e"
                    + join
                    + " WHERE e.book_id=:book AND e.kind=:kind AND e.id IN :ids"
                    + predicate
                ).bindparams(bindparam("ids", expanding=True))
                found = set(
                    connection.execute(
                        statement,
                        {
                            "book": self._book_id,
                            "kind": kind,
                            "ids": chunk,
                            "currency": scenario.limit.currency.code,
                        },
                    ).scalars()
                )
                if found != set(chunk):
                    raise ValidationError(
                        "Отбор бюджета должен ссылаться на счета и категории расходов этой книги."
                    )

    def _insert_payload(
        self, connection: Connection, revision: str, name: str, scenario: PlanningScenario
    ) -> None:
        connection.execute(
            text(
                "INSERT INTO planning_scenario_revisions "
                "(revision_id,scenario_kind,name,name_key,scenario_json) VALUES (:r,:kind,:n,:k,:s)"
            ),
            {
                "r": revision,
                "kind": planning_kind(scenario).value,
                "n": name,
                "k": name.casefold(),
                "s": planning_json(scenario),
            },
        )

    def save(
        self,
        name: str,
        scenario: PlanningScenario,
        *,
        identifier: str | None = None,
        expected_revision_id: str | None = None,
        is_deleted: bool = False,
        command_id: str | None = None,
    ) -> SavedPlanningScenario:
        name = validate_name(name)
        if len(name.casefold()) > 240:
            raise ValidationError("Сократите название бюджета или цели.")
        if type(is_deleted) is not bool:
            raise ValidationError("Некорректные данные условий планирования.")
        scenario = planning_from_json(planning_json(scenario))
        kind = planning_kind(scenario)
        command_id = str(uuid4()) if command_id is None else command_id
        require_identifier(command_id)
        if (identifier is None) != (expected_revision_id is None):
            raise ValidationError("Для изменения укажите ожидаемую версию условий планирования.")
        if identifier is not None:
            require_identifier(identifier)
        if expected_revision_id is not None:
            require_identifier(expected_revision_id)
        digest = hashlib.sha256(
            canonical_json(
                {
                    "kind": KIND + "-command",
                    "payload": definition_payload(name, scenario),
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
                return self._revision(connection, receipt["revision_id"])
            if identifier is not None:
                current = self._current(connection, identifier)
                if current.kind != kind:
                    raise ValidationError("Нельзя заменить бюджет целью или цель бюджетом.")
                if current.revision_id != expected_revision_id:
                    raise ValidationError(
                        "Условия планирования изменены. Обновите список и повторите действие."
                    )
            elif is_deleted:
                raise ValidationError("Нельзя архивировать ещё не созданную запись.")
            if not is_deleted:
                active, duplicate = connection.execute(
                    text("""SELECT count(*),COALESCE(max(p.name_key=:name),0)
                        FROM entities e JOIN entity_revisions r ON r.id=e.accepted_revision_id
                        JOIN planning_scenario_revisions p ON p.revision_id=r.id
                        WHERE e.book_id=:book AND e.kind='planning_scenario'
                            AND r.is_deleted=0 AND p.scenario_kind=:kind
                            AND (:identifier IS NULL OR e.id<>:identifier)"""),
                    {
                        "book": self._book_id,
                        "kind": kind.value,
                        "identifier": identifier,
                        "name": name.casefold(),
                    },
                ).one()
                if duplicate:
                    raise ValidationError("Активная запись с таким названием уже существует.")
                if active >= 1000:
                    raise ValidationError("Допустимо не более 1000 активных записей каждого вида.")
            self._scope(connection, scenario, current=not is_deleted)
            if identifier is None:
                identifier = str(uuid4())
                connection.execute(
                    text("INSERT INTO entities(id,book_id,kind) VALUES (:id,:book,:kind)"),
                    {"id": identifier, "book": self._book_id, "kind": KIND},
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
                    "hash": definition_hash(name, scenario, is_deleted, expected_revision_id),
                    "deleted": int(is_deleted),
                },
            )
            self._insert_payload(connection, revision, name, scenario)
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
            return self._revision(connection, revision)

    def check_integrity(self) -> None:
        with self._mutex, self._engine.connect() as connection:
            try:
                missing: int = connection.execute(
                    text(
                        "SELECT count(*) FROM entities "
                        "WHERE book_id=:book AND kind='planning_scenario' "
                        "AND accepted_revision_id IS NULL"
                    ),
                    {"book": self._book_id},
                ).scalar_one()
                if missing:
                    raise VaultError("Обнаружен непринятый набор условий планирования.")
                for value in self._items(connection, True):
                    self._history(connection, value.id)
            except (ValidationError, ValueError, TypeError, KeyError, AttributeError) as exc:
                raise VaultError("Проверка условий планирования не пройдена.") from exc
