"""Изолированное зашифрованное хранилище версий инвестиционных позиций."""

import hashlib
from collections.abc import Sequence
from threading import RLock
from uuid import uuid4

from sqlalchemy import Connection, Engine, text
from sqlalchemy.engine import RowMapping

from personal_finance.application.catalog import utc_now
from personal_finance.domain.errors import ValidationError, VaultError
from personal_finance.domain.investment_positions import (
    InvestmentPositionInputs,
    SavedInvestmentPosition,
    investment_position_from_json,
    investment_position_json,
)
from personal_finance.domain.ledger import require_identifier
from personal_finance.domain.models import Account, AccountKind
from personal_finance.infrastructure.security.keys import canonical_json

KIND = "investment_position"
_SELECT = """SELECT e.id,r.id AS revision_id,r.sealed,r.is_deleted,r.created_at,r.content_hash,
    p.account_id,p.security_id,p.security_code,p.position_json,
    (SELECT parent_id FROM revision_parents WHERE revision_id=r.id) AS parent_id
    FROM entities e JOIN entity_revisions r ON r.entity_id=e.id
    LEFT JOIN investment_position_revisions p ON p.revision_id=r.id
    WHERE e.book_id=:book AND e.kind='investment_position'"""


def _digest(payload: dict[str, object]) -> str:
    return hashlib.sha256(canonical_json(payload)).hexdigest()


def _content_hash(raw: str, deleted: bool, parent: str | None) -> str:
    return _digest(
        {"kind": KIND, "inputs": raw, "is_deleted": deleted, "parent_revision_id": parent}
    )


def _record(row: RowMapping) -> SavedInvestmentPosition:
    try:
        require_identifier(row["id"])
        require_identifier(row["revision_id"])
        raw = row["position_json"]
        inputs = investment_position_from_json(raw)
        if (
            row["sealed"] != 1
            or row["is_deleted"] not in (0, 1)
            or row["account_id"] != inputs.account_id
            or row["security_id"] != inputs.security.id
            or row["security_code"] != inputs.security.code
            or row["content_hash"] != _content_hash(raw, bool(row["is_deleted"]), row["parent_id"])
        ):
            raise ValueError
        return SavedInvestmentPosition(
            row["id"], row["revision_id"], inputs, bool(row["is_deleted"]), row["created_at"]
        )
    except (TypeError, ValueError, KeyError, ValidationError):
        raise VaultError("Нарушена целостность версии инвестиционной позиции.") from None


class SqlInvestmentPositions:
    def __init__(self, engine: Engine, book_id: str, replica_id: str, mutex: RLock) -> None:
        self._engine, self._book_id, self._replica_id, self._mutex = (
            engine,
            book_id,
            replica_id,
            mutex,
        )

    def _account(self, connection: Connection, identifier: str, *, archived: bool) -> Account:
        row = (
            connection.execute(
                text("""SELECT e.id,p.name,p.kind,p.institution_id,p.currency,r.id AS revision_id
                FROM entities e JOIN entity_revisions r ON r.id=e.accepted_revision_id
                JOIN account_revisions p ON p.revision_id=r.id
                WHERE e.id=:id AND e.book_id=:book AND e.kind='account' AND r.sealed=1
                    AND p.kind='investment' AND (:archived=1 OR r.is_deleted=0)"""),
                {"id": identifier, "book": self._book_id, "archived": int(archived)},
            )
            .mappings()
            .one_or_none()
        )
        if row is None:
            raise ValidationError("Выберите доступный инвестиционный счёт этой книги.")
        return Account(
            row["id"],
            row["name"],
            AccountKind(row["kind"]),
            row["institution_id"],
            row["currency"],
            row["revision_id"],
        )

    def _revision(self, connection: Connection, revision: str) -> SavedInvestmentPosition:
        row = (
            connection.execute(
                text(_SELECT + " AND r.id=:revision"),
                {"book": self._book_id, "revision": revision},
            )
            .mappings()
            .one_or_none()
        )
        if row is None:
            raise VaultError("Версия инвестиционной позиции отсутствует.")
        return _record(row)

    def _current(self, connection: Connection, identifier: str) -> SavedInvestmentPosition:
        row = (
            connection.execute(
                text(_SELECT + " AND e.id=:id AND r.id=e.accepted_revision_id"),
                {"book": self._book_id, "id": identifier},
            )
            .mappings()
            .one_or_none()
        )
        if row is None:
            raise ValidationError("Инвестиционная позиция не найдена в этой книге.")
        return _record(row)

    def get(self, identifier: str) -> SavedInvestmentPosition:
        require_identifier(identifier)
        with self._mutex, self._engine.connect() as connection:
            return self._current(connection, identifier)

    def items(self, *, include_archived: bool = False) -> list[SavedInvestmentPosition]:
        with self._mutex, self._engine.connect() as connection:
            return self._items(connection, include_archived=include_archived)

    def _items(
        self, connection: Connection, *, include_archived: bool
    ) -> list[SavedInvestmentPosition]:
        rows = connection.execute(
            text(
                _SELECT
                + """ AND r.id=e.accepted_revision_id AND (:archived=1 OR r.is_deleted=0)
                ORDER BY p.security_code,p.account_id,e.id"""
            ),
            {"book": self._book_id, "archived": int(include_archived)},
        ).mappings()
        return [_record(row) for row in rows]

    def _history(self, connection: Connection, identifier: str) -> list[SavedInvestmentPosition]:
        current = self._current(connection, identifier)
        rows = (
            connection.execute(
                text(_SELECT + " AND e.id=:id"),
                {"book": self._book_id, "id": identifier},
            )
            .mappings()
            .all()
        )
        records = {row["revision_id"]: (_record(row), row["parent_id"]) for row in rows}
        edges: int = connection.execute(
            text("""SELECT count(*) FROM revision_parents p
                JOIN entity_revisions r ON r.id=p.revision_id WHERE r.entity_id=:id"""),
            {"id": identifier},
        ).scalar_one()
        heads: Sequence[str] = (
            connection.execute(
                text("SELECT revision_id FROM revision_heads WHERE entity_id=:id"),
                {"id": identifier},
            )
            .scalars()
            .all()
        )
        if edges != len(records) - 1 or list(heads) != [current.revision_id]:
            raise VaultError("Нарушены связи версий инвестиционной позиции.")
        chain = []
        visited: set[str] = set()
        revision: str | None = current.revision_id
        while revision is not None:
            if revision in visited or revision not in records:
                raise VaultError("Нарушена история инвестиционной позиции.")
            visited.add(revision)
            record, revision = records[revision]
            chain.append(record)
        if len(chain) != len(records) or chain[-1].is_deleted:
            raise VaultError("История инвестиционной позиции неполна.")
        return list(reversed(chain))

    def history(self, identifier: str) -> list[SavedInvestmentPosition]:
        require_identifier(identifier)
        with self._mutex, self._engine.connect() as connection:
            return self._history(connection, identifier)

    @staticmethod
    def _identity(value: InvestmentPositionInputs) -> tuple[object, ...]:
        return value.account_id, value.security.id, value.security.kind, value.security.currency

    def save(
        self,
        inputs: InvestmentPositionInputs,
        *,
        identifier: str | None = None,
        expected_revision_id: str | None = None,
        is_deleted: bool = False,
        command_id: str | None = None,
    ) -> SavedInvestmentPosition:
        if not isinstance(inputs, InvestmentPositionInputs) or type(is_deleted) is not bool:
            raise ValidationError("Укажите условия инвестиционной позиции.")
        if (identifier is None) != (expected_revision_id is None):
            raise ValidationError("Для изменения укажите ожидаемую версию позиции.")
        if identifier is not None and expected_revision_id is not None:
            require_identifier(identifier)
            require_identifier(expected_revision_id)
        elif is_deleted:
            raise ValidationError("Нельзя архивировать ещё не созданную позицию.")
        command_id = str(uuid4()) if command_id is None else command_id
        require_identifier(command_id)
        raw = investment_position_json(inputs)
        digest = _digest(
            {
                "kind": KIND + "-command",
                "inputs": raw,
                "identifier": identifier,
                "expected_revision_id": expected_revision_id,
                "is_deleted": is_deleted,
            }
        )
        with self._mutex, self._engine.begin() as connection:
            receipt = (
                connection.execute(
                    text("SELECT command_hash,revision_id FROM command_receipts WHERE id=:id"),
                    {"id": command_id},
                )
                .mappings()
                .one_or_none()
            )
            if receipt is not None:
                if receipt["command_hash"] != digest:
                    raise ValidationError(
                        "Идентификатор действия уже использован для других данных."
                    )
                return self._revision(connection, receipt["revision_id"])
            if identifier is not None:
                current = self._current(connection, identifier)
                if current.revision_id != expected_revision_id:
                    raise ValidationError(
                        "Позиция изменилась. Обновите список и повторите действие."
                    )
                if self._identity(current.inputs) != self._identity(inputs):
                    raise ValidationError(
                        "Счёт, вид, валюта и идентификатор бумаги сохраняются в истории."
                    )
            account = self._account(connection, inputs.account_id, archived=is_deleted)
            inputs.check_for(account)
            if not is_deleted and self._duplicate(connection, inputs, identifier):
                raise ValidationError("На этом счёте уже есть активная позиция с таким кодом.")
            if identifier is None:
                identifier = str(uuid4())
                connection.execute(
                    text("INSERT INTO entities(id,book_id,kind) VALUES (:id,:book,:kind)"),
                    {"id": identifier, "book": self._book_id, "kind": KIND},
                )
            revision = self._append(
                connection,
                identifier,
                inputs,
                raw,
                expected_revision_id,
                is_deleted,
                command_id,
                digest,
            )
            return self._revision(connection, revision)

    def _duplicate(
        self, connection: Connection, inputs: InvestmentPositionInputs, identifier: str | None
    ) -> bool:
        return bool(
            connection.execute(
                text("""SELECT EXISTS(SELECT 1 FROM entities e
                JOIN entity_revisions r ON r.id=e.accepted_revision_id
                JOIN investment_position_revisions p ON p.revision_id=r.id
                WHERE e.book_id=:book AND e.kind='investment_position' AND r.is_deleted=0
                AND p.account_id=:account AND p.security_code=:code
                AND (:id IS NULL OR e.id<>:id))"""),
                {
                    "book": self._book_id,
                    "account": inputs.account_id,
                    "code": inputs.security.code,
                    "id": identifier,
                },
            ).scalar_one()
        )

    def _append(
        self,
        connection: Connection,
        identifier: str,
        inputs: InvestmentPositionInputs,
        raw: str,
        parent: str | None,
        deleted: bool,
        command: str,
        digest: str,
    ) -> str:
        revision, group, created = str(uuid4()), str(uuid4()), utc_now()
        sequence: int = connection.execute(
            text(
                "UPDATE replicas SET sequence=sequence+1 "
                "WHERE id=:id AND book_id=:book RETURNING sequence"
            ),
            {"id": self._replica_id, "book": self._book_id},
        ).scalar_one()
        values = {
            "e": identifier,
            "r": revision,
            "g": group,
            "book": self._book_id,
            "created": created,
            "replica": self._replica_id,
            "seq": sequence,
            "hash": _content_hash(raw, deleted, parent),
            "deleted": int(deleted),
            "account": inputs.account_id,
            "security": inputs.security.id,
            "code": inputs.security.code,
            "raw": raw,
            "parent": parent,
            "change": str(uuid4()),
            "command": command,
            "digest": digest,
        }
        connection.execute(
            text("INSERT INTO change_groups(id,book_id,created_at) VALUES (:g,:book,:created)"),
            values,
        )
        connection.execute(
            text("""INSERT INTO entity_revisions
                (id,entity_id,replica_id,sequence,group_id,created_at,content_hash,is_deleted)
                VALUES (:r,:e,:replica,:seq,:g,:created,:hash,:deleted)"""),
            values,
        )
        connection.execute(
            text("""INSERT INTO investment_position_revisions
                (revision_id,account_id,security_id,security_code,position_json)
                VALUES (:r,:account,:security,:code,:raw)"""),
            values,
        )
        if parent is not None:
            connection.execute(
                text("INSERT INTO revision_parents(revision_id,parent_id) VALUES (:r,:parent)"),
                values,
            )
        connection.execute(text("UPDATE entity_revisions SET sealed=1 WHERE id=:r"), values)
        connection.execute(text("UPDATE entities SET accepted_revision_id=:r WHERE id=:e"), values)
        connection.execute(text("DELETE FROM revision_heads WHERE entity_id=:e"), values)
        connection.execute(
            text("INSERT INTO revision_heads(entity_id,revision_id) VALUES (:e,:r)"), values
        )
        connection.execute(
            text("""INSERT INTO change_log(id,replica_id,sequence,group_id,revision_id)
                VALUES (:change,:replica,:seq,:g,:r)"""),
            values,
        )
        connection.execute(
            text("""INSERT INTO command_receipts(id,command_hash,entity_id,revision_id)
                VALUES (:command,:digest,:e,:r)"""),
            values,
        )
        return revision

    def check_integrity(self) -> None:
        with self._mutex, self._engine.connect() as connection:
            expected: int = connection.execute(
                text(
                    "SELECT count(*) FROM entities WHERE book_id=:book "
                    "AND kind='investment_position'"
                ),
                {"book": self._book_id},
            ).scalar_one()
            records = self._items(connection, include_archived=True)
            if len(records) != expected:
                raise VaultError("Не найдены принятые версии инвестиционных позиций.")
            for current in records:
                identity = self._identity(current.inputs)
                try:
                    account = self._account(connection, current.inputs.account_id, archived=True)
                    for version in self._history(connection, current.id):
                        if self._identity(version.inputs) != identity:
                            raise ValidationError("Идентичность позиции изменилась.")
                        version.inputs.check_for(account)
                except ValidationError as error:
                    raise VaultError("Нарушена область инвестиционной позиции.") from error
