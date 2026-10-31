"""Атомарные неизменяемые сценарии вкладов и защита повторных команд."""

import hashlib
from threading import RLock
from typing import Any
from uuid import uuid4

from sqlalchemy import Connection, Engine, text
from sqlalchemy.engine import RowMapping

from personal_finance.application.catalog import utc_now
from personal_finance.domain.deposit_scenarios import (
    SavedDepositScenario,
    scenario_from_json,
    scenario_json,
    scenario_payload,
)
from personal_finance.domain.deposits import DepositScenario, forecast_deposit
from personal_finance.domain.errors import ValidationError, VaultError
from personal_finance.domain.ledger import require_identifier
from personal_finance.domain.models import validate_name
from personal_finance.infrastructure.security.keys import canonical_json

KIND = "deposit_scenario"


def definition_payload(name: str, scenario: DepositScenario) -> dict[str, Any]:
    return {"name": name, "scenario": scenario_payload(scenario)}


def definition_hash(name: str, scenario: DepositScenario, deleted: bool, parent: str | None) -> str:
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


class SqlDepositScenariosRepository:
    def __init__(self, engine: Engine, book_id: str, replica_id: str, mutex: RLock) -> None:
        self._engine, self._book_id, self._replica_id, self._mutex = (
            engine,
            book_id,
            replica_id,
            mutex,
        )

    def _select(self) -> str:
        return """SELECT e.id,r.id AS revision_id,r.sealed,r.is_deleted,r.created_at,r.content_hash,
            p.name,p.name_key,p.scenario_json FROM entities e
            JOIN entity_revisions r ON r.entity_id=e.id
            LEFT JOIN deposit_scenario_revisions p ON p.revision_id=r.id
            WHERE e.book_id=:book AND e.kind='deposit_scenario'"""

    def _record(self, row: RowMapping) -> SavedDepositScenario:
        require_identifier(row["id"])
        require_identifier(row["revision_id"])
        name = validate_name(row["name"])
        if (
            row["sealed"] != 1
            or row["is_deleted"] not in (0, 1)
            or name != row["name"]
            or name.casefold() != row["name_key"]
        ):
            raise VaultError("Неверное состояние версии сценария вклада.")
        scenario = scenario_from_json(row["scenario_json"])
        return SavedDepositScenario(
            row["id"],
            row["revision_id"],
            name,
            scenario,
            bool(row["is_deleted"]),
            row["created_at"],
        )

    def _revision(self, connection: Connection, revision: str) -> SavedDepositScenario:
        row = (
            connection.execute(
                text(self._select() + " AND r.id=:revision"),
                {"book": self._book_id, "revision": revision},
            )
            .mappings()
            .first()
        )
        if row is None:
            raise ValidationError("Версия сценария вклада не найдена в этой книге.")
        return self._record(row)

    def _current(self, connection: Connection, identifier: str) -> SavedDepositScenario:
        row = (
            connection.execute(
                text(self._select() + " AND e.id=:id AND r.id=e.accepted_revision_id"),
                {"book": self._book_id, "id": identifier},
            )
            .mappings()
            .first()
        )
        if row is None:
            raise ValidationError("Сценарий вклада не найден в этой книге.")
        return self._record(row)

    def _items(self, connection: Connection, archived: bool) -> list[SavedDepositScenario]:
        rows = connection.execute(
            text(
                self._select()
                + " AND r.id=e.accepted_revision_id AND (:archived=1 OR r.is_deleted=0) "
                "ORDER BY p.name_key,e.id"
            ),
            {"book": self._book_id, "archived": int(archived)},
        ).mappings()
        return [self._record(row) for row in rows]

    def items(self, *, include_archived: bool = False) -> list[SavedDepositScenario]:
        if type(include_archived) is not bool:
            raise ValidationError("Некорректный выбор архивных сценариев.")
        with self._mutex, self._engine.connect() as connection:
            return self._items(connection, include_archived)

    def get(self, identifier: str) -> SavedDepositScenario:
        require_identifier(identifier)
        with self._mutex, self._engine.connect() as connection:
            return self._current(connection, identifier)

    def _history(self, connection: Connection, identifier: str) -> list[SavedDepositScenario]:
        rows = list(
            connection.execute(
                text(self._select() + " AND e.id=:id"),
                {"book": self._book_id, "id": identifier},
            ).mappings()
        )
        records = {row["revision_id"]: self._record(row) for row in rows}
        hashes = {row["revision_id"]: row["content_hash"] for row in rows}
        if not records:
            raise ValidationError("История сценария вклада не найдена в этой книге.")
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
                raise VaultError("Нарушена последовательность версий сценария вклада.")
            visited.add(current)
            value = records[current]
            chain.append(value)
            parent = parents[current][0] if parents[current] else None
            if hashes[current] != definition_hash(
                value.name, value.scenario, value.is_deleted, parent
            ):
                raise VaultError("Содержимое сценария вклада не соответствует хешу.")
            current = parent
        chain.reverse()
        if not chain or len(visited) != len(records) or chain[0].is_deleted:
            raise VaultError("Непринятые ветви истории сценария вклада.")
        heads: list[str] = list(
            connection.execute(
                text("SELECT revision_id FROM revision_heads WHERE entity_id=:id"),
                {"id": identifier},
            ).scalars()
        )
        if heads != [chain[-1].revision_id]:
            raise VaultError("Неверная принятая ветвь сценария вклада.")
        return chain

    def history(self, identifier: str) -> list[SavedDepositScenario]:
        require_identifier(identifier)
        with self._mutex, self._engine.connect() as connection:
            return self._history(connection, identifier)

    def _insert_payload(
        self, connection: Connection, revision: str, name: str, scenario: DepositScenario
    ) -> None:
        connection.execute(
            text(
                "INSERT INTO deposit_scenario_revisions "
                "(revision_id,name,name_key,scenario_json) VALUES (:r,:n,:k,:s)"
            ),
            {"r": revision, "n": name, "k": name.casefold(), "s": scenario_json(scenario)},
        )

    def save(
        self,
        name: str,
        scenario: DepositScenario,
        *,
        identifier: str | None = None,
        expected_revision_id: str | None = None,
        is_deleted: bool = False,
        command_id: str | None = None,
    ) -> SavedDepositScenario:
        name = validate_name(name)
        if type(is_deleted) is not bool or not isinstance(scenario, DepositScenario):
            raise ValidationError("Некорректные данные сценария вклада.")
        forecast_deposit(scenario)
        command_id = str(uuid4()) if command_id is None else command_id
        require_identifier(command_id)
        if (identifier is None) != (expected_revision_id is None):
            raise ValidationError("Для изменения укажите ожидаемую версию сценария вклада.")
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
                if current.revision_id != expected_revision_id:
                    raise ValidationError(
                        "Сценарий вклада изменён. Обновите список и повторите действие."
                    )
            elif is_deleted:
                raise ValidationError("Нельзя архивировать ещё не созданную запись.")
            if not is_deleted and any(
                value.id != identifier and value.name.casefold() == name.casefold()
                for value in self._items(connection, False)
            ):
                raise ValidationError("Активная запись с таким названием уже существует.")
            if (
                not is_deleted
                and sum(v.id != identifier for v in self._items(connection, False)) >= 1000
            ):
                raise ValidationError("Допустимо не более 1000 активных сценариев вкладов.")
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
                        "WHERE book_id=:book AND kind='deposit_scenario' "
                        "AND accepted_revision_id IS NULL"
                    ),
                    {"book": self._book_id},
                ).scalar_one()
                if missing:
                    raise VaultError("Обнаружен непринятый сценарий вклада.")
                for value in self._items(connection, True):
                    self._history(connection, value.id)
            except (ValidationError, ValueError, TypeError, KeyError, AttributeError) as exc:
                raise VaultError("Проверка сценариев вкладов не пройдена.") from exc
