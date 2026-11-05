"""Бонусные версии в общем журнале команд; денежные таблицы не изменяются."""

import hashlib
from collections.abc import Sequence
from contextlib import nullcontext
from datetime import date
from threading import RLock
from typing import Any
from uuid import UUID, uuid4, uuid5

from sqlalchemy import Connection, Engine, text
from sqlalchemy.engine import RowMapping

from personal_finance.application.catalog import utc_now
from personal_finance.domain.bonuses import (
    BonusDraft,
    BonusEntry,
    BonusKind,
    BonusProgram,
    program_name,
)
from personal_finance.domain.errors import ValidationError, VaultError
from personal_finance.domain.ledger import require_identifier
from personal_finance.infrastructure.security.keys import canonical_json

TABLES = {"bonus_program": "bonus_program_revisions", "bonus_entry": "bonus_entry_revisions"}


def entry_payload(draft: BonusDraft) -> dict[str, Any]:
    return {
        "program_id": draft.program_id,
        "occurred_on": draft.occurred_on.isoformat(),
        "kind": draft.kind.value,
        "units_minor": draft.units_minor,
        "category_id": draft.category_id,
        "operation_id": draft.operation_id,
        "note": draft.note,
        "source_key": draft.source_key,
        "bank_category": draft.bank_category,
    }


def content_hash(kind: str, payload: dict[str, Any], deleted: bool, parent: str | None) -> str:
    return hashlib.sha256(
        canonical_json(
            {"kind": kind, "payload": payload, "is_deleted": deleted, "parent_revision_id": parent}
        )
    ).hexdigest()


class SqlBonusRepository:
    def __init__(self, engine: Engine, book_id: str, replica_id: str, mutex: RLock) -> None:
        self._engine, self._book_id, self._replica_id, self._mutex = (
            engine,
            book_id,
            replica_id,
            mutex,
        )

    def _select(self, kind: str) -> str:
        table = TABLES[kind]
        columns = (
            "p.name,p.name_key"
            if kind == "bonus_program"
            else "p.program_id,p.occurred_on,p.kind,p.units_minor,p.category_id,p.operation_id,"
            "p.note,p.source_key,p.bank_category"
        )
        return f"""SELECT e.id,r.id AS revision_id,r.is_deleted,r.created_at,r.sealed,
            r.content_hash,{columns} FROM entities e JOIN entity_revisions r ON r.entity_id=e.id
            JOIN {table} p ON p.revision_id=r.id WHERE e.book_id=:book AND e.kind='{kind}'"""

    def _record(self, kind: str, row: RowMapping) -> BonusProgram | BonusEntry:
        try:
            require_identifier(row["id"])
            require_identifier(row["revision_id"])
            if row["sealed"] != 1 or row["is_deleted"] not in (0, 1):
                raise ValueError
            if kind == "bonus_program":
                name = program_name(row["name"])
                if name != row["name"] or name.casefold() != row["name_key"]:
                    raise ValueError
                return BonusProgram(
                    row["id"], row["revision_id"], name, bool(row["is_deleted"]), row["created_at"]
                )
            draft = BonusDraft(
                row["program_id"],
                date.fromisoformat(row["occurred_on"]),
                BonusKind(row["kind"]),
                row["units_minor"],
                row["category_id"],
                row["operation_id"],
                row["note"],
                row["source_key"],
                row["bank_category"],
            )
            return BonusEntry(
                row["id"], row["revision_id"], draft, bool(row["is_deleted"]), row["created_at"]
            )
        except (ValueError, TypeError, KeyError, ValidationError):
            raise VaultError("Некорректная версия бонусной записи.") from None

    def _revision(
        self, connection: Connection, kind: str, revision: str
    ) -> BonusProgram | BonusEntry:
        row = (
            connection.execute(
                text(self._select(kind) + " AND r.id=:revision"),
                {"book": self._book_id, "revision": revision},
            )
            .mappings()
            .first()
        )
        if row is None:
            raise ValidationError("Бонусная версия не найдена в этой книге.")
        return self._record(kind, row)

    def _current(
        self, connection: Connection, kind: str, identifier: str
    ) -> BonusProgram | BonusEntry:
        row = (
            connection.execute(
                text(self._select(kind) + " AND e.id=:id AND r.id=e.accepted_revision_id"),
                {"book": self._book_id, "id": identifier},
            )
            .mappings()
            .first()
        )
        if row is None:
            raise ValidationError("Бонусная запись не найдена в этой книге.")
        return self._record(kind, row)

    def programs(self, *, include_archived: bool = False) -> list[BonusProgram]:
        with self._mutex, self._engine.connect() as c:
            rows = c.execute(
                text(
                    self._select("bonus_program")
                    + " AND r.id=e.accepted_revision_id"
                    + ("" if include_archived else " AND r.is_deleted=0")
                ),
                {"book": self._book_id},
            ).mappings()
            result = [self._record("bonus_program", r) for r in rows]
            if any(not isinstance(v, BonusProgram) for v in result):
                raise VaultError("Неверный вид программы.")
            return sorted(
                [v for v in result if isinstance(v, BonusProgram)],
                key=lambda v: (v.name.casefold(), v.id),
            )

    def entries(self, *, include_deleted: bool = False) -> list[BonusEntry]:
        with self._mutex, self._engine.connect() as c:
            rows = c.execute(
                text(
                    self._select("bonus_entry")
                    + " AND r.id=e.accepted_revision_id"
                    + ("" if include_deleted else " AND r.is_deleted=0")
                ),
                {"book": self._book_id},
            ).mappings()
            result = [self._record("bonus_entry", r) for r in rows]
            return sorted(
                [v for v in result if isinstance(v, BonusEntry)],
                key=lambda v: (v.draft.occurred_on, v.created_at, v.id),
                reverse=True,
            )

    def history(self, identifier: str) -> list[BonusProgram | BonusEntry]:
        require_identifier(identifier)
        with self._mutex, self._engine.connect() as c:
            kind = c.execute(
                text("SELECT kind FROM entities WHERE id=:id AND book_id=:book"),
                {"id": identifier, "book": self._book_id},
            ).scalar_one_or_none()
            if not isinstance(kind, str) or kind not in TABLES:
                raise ValidationError("Бонусная запись не найдена в этой книге.")
            rows = c.execute(
                text(self._select(kind) + " AND e.id=:id ORDER BY r.created_at,r.rowid"),
                {"book": self._book_id, "id": identifier},
            ).mappings()
            return [self._record(kind, row) for row in rows]

    def _links(self, c: Connection, draft: BonusDraft, *, active: bool) -> None:
        program = self._current(c, "bonus_program", draft.program_id)
        if active and program.is_deleted:
            raise ValidationError("Выбранная программа в архиве.")
        if draft.category_id is not None:
            good: int = c.execute(
                text("""SELECT count(*) FROM entities e JOIN category_revisions p
                ON p.revision_id=e.accepted_revision_id WHERE e.id=:id AND e.book_id=:book
                AND e.kind='category' AND p.kind='expense'"""),
                {"id": draft.category_id, "book": self._book_id},
            ).scalar_one()
            if not good:
                raise ValidationError("Выберите категорию расходов этой книги.")
        if draft.operation_id is not None:
            operation_found: int = c.execute(
                text(
                    "SELECT count(*) FROM entities "
                    "WHERE id=:id AND book_id=:book AND kind='operation'"
                ),
                {"id": draft.operation_id, "book": self._book_id},
            ).scalar_one()
            if not operation_found:
                raise ValidationError("Связанная денежная операция не найдена в этой книге.")

    def save_program(
        self,
        name: str,
        *,
        identifier: str | None = None,
        expected_revision_id: str | None = None,
        is_deleted: bool = False,
        command_id: str | None = None,
    ) -> BonusProgram:
        value = self._save(
            "bonus_program",
            {"name": program_name(name)},
            None,
            identifier,
            expected_revision_id,
            is_deleted,
            command_id,
        )
        if not isinstance(value, BonusProgram):
            raise VaultError("Неверный вид бонусной программы.")
        return value

    def save_entry(
        self,
        draft: BonusDraft,
        *,
        identifier: str | None = None,
        expected_revision_id: str | None = None,
        is_deleted: bool = False,
        command_id: str | None = None,
    ) -> BonusEntry:
        if not isinstance(draft, BonusDraft):
            raise ValidationError("Некорректная бонусная запись.")
        value = self._save(
            "bonus_entry",
            entry_payload(draft),
            draft,
            identifier,
            expected_revision_id,
            is_deleted,
            command_id,
        )
        if not isinstance(value, BonusEntry):
            raise VaultError("Неверный вид бонусной записи.")
        return value

    def _save(
        self,
        kind: str,
        payload: dict[str, Any],
        draft: BonusDraft | None,
        identifier: str | None,
        expected: str | None,
        deleted: bool,
        command: str | None,
        *,
        _connection: Connection | None = None,
        _group: tuple[str, str] | None = None,
    ) -> BonusProgram | BonusEntry:
        if type(deleted) is not bool or (identifier is None) != (expected is None):
            raise ValidationError("Для изменения укажите ожидаемую версию бонусной записи.")
        for value in (identifier, expected):
            if value is not None:
                require_identifier(value)
        command = str(uuid4()) if command is None else command
        require_identifier(command)
        digest = hashlib.sha256(
            canonical_json(
                {
                    "kind": kind + "-command",
                    "payload": payload,
                    "identifier": identifier,
                    "expected_revision_id": expected,
                    "is_deleted": deleted,
                }
            )
        ).hexdigest()
        with (
            self._mutex,
            self._engine.begin() if _connection is None else nullcontext(_connection) as c,
        ):
            receipt = (
                c.execute(
                    text("SELECT command_hash,revision_id FROM command_receipts WHERE id=:id"),
                    {"id": command},
                )
                .mappings()
                .first()
            )
            if receipt:
                if receipt["command_hash"] != digest:
                    raise ValidationError(
                        "Идентификатор действия уже использован для других данных."
                    )
                return self._revision(c, kind, receipt["revision_id"])
            if identifier is not None:
                current = self._current(c, kind, identifier)
                if current.revision_id != expected:
                    raise ValidationError(
                        "Запись изменилась. Обновите список и повторите действие."
                    )
            elif deleted:
                raise ValidationError("Нельзя отменить ещё не созданную запись.")
            if kind == "bonus_program" and not deleted:
                names = c.execute(
                    text("""SELECT e.id,p.name_key FROM entities e JOIN entity_revisions r
                    ON r.id=e.accepted_revision_id JOIN bonus_program_revisions p ON
        p.revision_id=r.id
                    WHERE e.book_id=:book AND e.kind='bonus_program' AND r.is_deleted=0"""),
                    {"book": self._book_id},
                ).all()
                if any(
                    row[0] != identifier and row[1] == payload["name"].casefold() for row in names
                ):
                    raise ValidationError("Активная программа с таким названием уже существует.")
                if sum(row[0] != identifier for row in names) >= 1000:
                    raise ValidationError("Допустимо не более 1000 программ.")
            if draft is not None:
                self._links(c, draft, active=not deleted)
                if draft.source_key is not None:
                    other: Sequence[str] = (
                        c.execute(
                            text("""SELECT e.id FROM entities e JOIN bonus_entry_revisions p
                        ON p.revision_id=e.accepted_revision_id WHERE e.book_id=:book
                        AND e.kind='bonus_entry' AND p.source_key=:source"""),
                            {"book": self._book_id, "source": draft.source_key},
                        )
                        .scalars()
                        .all()
                    )
                    if any(value != identifier for value in other):
                        raise ValidationError(
                            "Эта бонусная подпись уже учтена. Исправьте существующую запись."
                        )
            if identifier is None:
                identifier = str(uuid4())
                c.execute(
                    text("INSERT INTO entities(id,book_id,kind)VALUES(:id,:book,:kind)"),
                    {"id": identifier, "book": self._book_id, "kind": kind},
                )
            revision = str(uuid4())
            group, created = _group if _group is not None else (str(uuid4()), utc_now())
            sequence: int = c.execute(
                text(
                    "UPDATE replicas SET sequence=sequence+1 WHERE id=:id AND book_id=:book "
                    "RETURNING sequence"
                ),
                {"id": self._replica_id, "book": self._book_id},
            ).scalar_one()
            if _group is None:
                c.execute(
                    text(
                        "INSERT INTO change_groups(id,book_id,created_at)VALUES(:id,:book,:created)"
                    ),
                    {"id": group, "book": self._book_id, "created": created},
                )
            c.execute(
                text("""INSERT INTO entity_revisions
                (id,entity_id,replica_id,sequence,group_id,created_at,content_hash,is_deleted)
                VALUES(:r,:e,:replica,:sequence,:g,:created,:hash,:deleted)"""),
                {
                    "r": revision,
                    "e": identifier,
                    "replica": self._replica_id,
                    "sequence": sequence,
                    "g": group,
                    "created": created,
                    "hash": content_hash(kind, payload, deleted, expected),
                    "deleted": int(deleted),
                },
            )
            if kind == "bonus_program":
                c.execute(
                    text(
                        "INSERT INTO "
                        "bonus_program_revisions(revision_id,name,name_key)VALUES(:r,:name,:key)"
                    ),
                    {"r": revision, "name": payload["name"], "key": payload["name"].casefold()},
                )
            else:
                columns = tuple(payload)
                c.execute(
                    text(
                        "INSERT INTO bonus_entry_revisions(revision_id,"
                        + ",".join(columns)
                        + ")VALUES(:r,"
                        + ",".join(":" + name for name in columns)
                        + ")"
                    ),
                    {"r": revision, **payload},
                )
            if expected:
                c.execute(
                    text("INSERT INTO revision_parents(revision_id,parent_id)VALUES(:r,:p)"),
                    {"r": revision, "p": expected},
                )
            c.execute(text("UPDATE entity_revisions SET sealed=1 WHERE id=:id"), {"id": revision})
            c.execute(
                text("UPDATE entities SET accepted_revision_id=:r WHERE id=:id"),
                {"r": revision, "id": identifier},
            )
            c.execute(text("DELETE FROM revision_heads WHERE entity_id=:id"), {"id": identifier})
            c.execute(
                text("INSERT INTO revision_heads(entity_id,revision_id)VALUES(:e,:r)"),
                {"e": identifier, "r": revision},
            )
            c.execute(
                text(
                    "INSERT INTO "
                    "change_log(id,replica_id,sequence,group_id,revision_id)VALUES(:id,:replica,:seq,:g,:r)"
                ),
                {
                    "id": str(uuid4()),
                    "replica": self._replica_id,
                    "seq": sequence,
                    "g": group,
                    "r": revision,
                },
            )
            c.execute(
                text(
                    "INSERT INTO "
                    "command_receipts(id,command_hash,entity_id,revision_id)VALUES(:id,:hash,:e,:r)"
                ),
                {"id": command, "hash": digest, "e": identifier, "r": revision},
            )
            return self._revision(c, kind, revision)

    def save_entries(
        self, drafts: tuple[BonusDraft, ...], *, command_id: str | None = None
    ) -> tuple[BonusEntry, ...]:
        if (
            not isinstance(drafts, tuple)
            or not 1 <= len(drafts) <= 100
            or any(not isinstance(d, BonusDraft) for d in drafts)
        ):
            raise ValidationError("Выберите от 1 до 100 допустимых бонусных записей.")
        command = str(uuid4()) if command_id is None else command_id
        require_identifier(command)
        digest = hashlib.sha256(
            canonical_json({"kind": "bonus-batch", "payload": [entry_payload(d) for d in drafts]})
        ).hexdigest()
        with self._mutex, self._engine.begin() as c:
            receipt = c.execute(
                text("SELECT command_hash FROM command_receipts WHERE id=:id"), {"id": command}
            ).scalar_one_or_none()
            if receipt is not None and receipt != digest:
                raise ValidationError(
                    "Идентификатор бонусного пакета уже использован для других данных."
                )
            children = [str(uuid5(UUID(command), str(i))) for i in range(len(drafts))]
            for child in children:
                found = c.execute(
                    text("SELECT id FROM command_receipts WHERE id=:id"), {"id": child}
                ).scalar_one_or_none()
                if receipt is None and found is not None:
                    raise ValidationError("Идентификатор строки пакета уже использован.")
                if receipt is not None and found is None:
                    raise VaultError("Неполная квитанция бонусного пакета.")
            group = str(uuid4()), utc_now()
            if receipt is None:
                c.execute(
                    text(
                        "INSERT INTO change_groups(id,book_id,created_at)VALUES(:id,:book,:created)"
                    ),
                    {"id": group[0], "book": self._book_id, "created": group[1]},
                )
            result = []
            for child, draft in zip(children, drafts, strict=True):
                value = self._save(
                    "bonus_entry",
                    entry_payload(draft),
                    draft,
                    None,
                    None,
                    False,
                    child,
                    _connection=c,
                    _group=group,
                )
                if not isinstance(value, BonusEntry):
                    raise VaultError("Неверный результат бонусного пакета.")
                result.append(value)
            if receipt is None:
                c.execute(
                    text(
                        "INSERT INTO command_receipts(id,command_hash,entity_id,revision_id)"
                        "VALUES(:id,:hash,:e,:r)"
                    ),
                    {
                        "id": command,
                        "hash": digest,
                        "e": result[-1].id,
                        "r": result[-1].revision_id,
                    },
                )
            return tuple(result)

    def check_integrity(self) -> None:
        with self._mutex, self._engine.connect() as c:
            sources = set()
            for kind in TABLES:
                rows = c.execute(text(self._select(kind)), {"book": self._book_id}).mappings().all()
                for row in rows:
                    record = self._record(kind, row)
                    parents: Sequence[str] = (
                        c.execute(
                            text("SELECT parent_id FROM revision_parents WHERE revision_id=:r"),
                            {"r": record.revision_id},
                        )
                        .scalars()
                        .all()
                    )
                    if len(parents) > 1:
                        raise VaultError("Несколько родителей бонусной версии.")
                    payload = (
                        {"name": record.name}
                        if isinstance(record, BonusProgram)
                        else entry_payload(record.draft)
                    )
                    expected = content_hash(
                        kind, payload, record.is_deleted, parents[0] if parents else None
                    )
                    if row["content_hash"] != expected:
                        raise VaultError("Контрольная сумма бонусной версии не совпала.")
                    if isinstance(record, BonusEntry):
                        self._links(c, record.draft, active=False)
            active = c.execute(
                text(self._select("bonus_entry") + " AND r.id=e.accepted_revision_id"),
                {"book": self._book_id},
            ).mappings()
            for row in active:
                key = row["source_key"]
                if key is not None:
                    if key in sources:
                        raise VaultError("Повтор бонусной подписи OCR.")
                    sources.add(key)
