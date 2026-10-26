"""Закрытие месяцев в общей атомарной истории книги."""

import hashlib
from datetime import date
from threading import RLock
from uuid import UUID, uuid4, uuid5

from sqlalchemy import Connection, Engine, text
from sqlalchemy.engine import RowMapping

from personal_finance.application.catalog import utc_now
from personal_finance.domain.errors import ValidationError, VaultError
from personal_finance.domain.ledger import require_identifier
from personal_finance.domain.periods import AccountingMonth, PeriodRevision, validate_period_change
from personal_finance.infrastructure.security.keys import canonical_json


def period_id(book_id: str, month: AccountingMonth) -> str:
    return str(uuid5(UUID(book_id), "period:" + month.key))


def period_hash(month: AccountingMonth, is_closed: bool, reason: str, parent: str | None) -> str:
    return hashlib.sha256(
        canonical_json(
            {
                "kind": "period",
                "month": month.key,
                "is_closed": is_closed,
                "reason": reason,
                "parent_revision_id": parent,
            }
        )
    ).hexdigest()


def require_open_dates(connection: Connection, book_id: str, *dates: date) -> None:
    for month in {AccountingMonth.from_date(value) for value in dates}:
        closed = connection.execute(
            text("""SELECT 1 FROM entities e
            JOIN period_revisions p ON p.revision_id=e.accepted_revision_id
            WHERE e.book_id=:book AND e.kind='period' AND p.month=:month AND p.is_closed=1"""),
            {"book": book_id, "month": month.key},
        ).first()
        if closed:
            raise ValidationError(
                f"Период {month.title} закрыт. Сначала явно откройте его в «Периоды»."
            )


class SqlPeriodRepository:
    def __init__(self, engine: Engine, book_id: str, replica_id: str, mutex: RLock) -> None:
        self._engine, self._book_id, self._replica_id, self._mutex = (
            engine,
            book_id,
            replica_id,
            mutex,
        )

    def _select(self) -> str:
        return """SELECT e.id,r.id AS revision_id,r.sealed,r.is_deleted,r.content_hash,
            r.created_at,p.month,p.is_closed,p.reason FROM entities e
            JOIN entity_revisions r ON r.entity_id=e.id
            LEFT JOIN period_revisions p ON p.revision_id=r.id
            WHERE e.book_id=:book AND e.kind='period'"""

    def _record(self, row: RowMapping) -> PeriodRevision:
        month = AccountingMonth.parse(row["month"])
        if row["sealed"] != 1 or row["is_deleted"] != 0 or row["is_closed"] not in (0, 1):
            raise VaultError("Неверное состояние версии периода.")
        reason = validate_period_change(month, bool(row["is_closed"]), row["reason"])
        if reason != row["reason"] or row["id"] != period_id(self._book_id, month):
            raise VaultError("Неверная идентичность периода.")
        return PeriodRevision(
            row["id"], row["revision_id"], month, bool(row["is_closed"]), reason, row["created_at"]
        )

    def _revision(self, connection: Connection, revision: str) -> PeriodRevision:
        row = (
            connection.execute(
                text(self._select() + " AND r.id=:revision"),
                {"book": self._book_id, "revision": revision},
            )
            .mappings()
            .first()
        )
        if row is None:
            raise ValidationError("Версия периода не найдена в этой книге.")
        return self._record(row)

    def states(self) -> list[PeriodRevision]:
        with self._mutex, self._engine.connect() as connection:
            rows = connection.execute(
                text(self._select() + " AND r.id=e.accepted_revision_id ORDER BY p.month DESC"),
                {"book": self._book_id},
            ).mappings()
            return [self._record(row) for row in rows]

    def _history(self, connection: Connection, month: AccountingMonth) -> list[PeriodRevision]:
        identifier = period_id(self._book_id, month)
        rows = list(
            connection.execute(
                text(self._select() + " AND e.id=:id"), {"book": self._book_id, "id": identifier}
            ).mappings()
        )
        records = {row["revision_id"]: self._record(row) for row in rows}
        if not records:
            return []
        parents: dict[str, list[str]] = {revision: [] for revision in records}
        for link in connection.execute(
            text("""SELECT p.revision_id,p.parent_id
            FROM revision_parents p JOIN entity_revisions r ON r.id=p.revision_id
            WHERE r.entity_id=:id"""),
            {"id": identifier},
        ).mappings():
            parents[link["revision_id"]].append(link["parent_id"])
        current: str | None = connection.execute(
            text("SELECT accepted_revision_id FROM entities WHERE id=:id"), {"id": identifier}
        ).scalar_one()
        chain = []
        visited = set()
        while current is not None:
            if current not in records or current in visited or len(parents[current]) > 1:
                raise VaultError("Нарушена последовательность версий периода.")
            visited.add(current)
            value = records[current]
            chain.append(value)
            parent = parents[current][0] if parents[current] else None
            row = next(row for row in rows if row["revision_id"] == current)
            if row["content_hash"] != period_hash(
                value.month, value.is_closed, value.reason, parent
            ):
                raise VaultError("Содержимое периода не соответствует контрольному хешу.")
            current = parent
        chain.reverse()
        if len(visited) != len(records) or any(
            value.month != month or value.is_closed != (index % 2 == 0)
            for index, value in enumerate(chain)
        ):
            raise VaultError("Нарушена история закрытия и открытия периода.")
        heads: list[str] = list(
            connection.execute(
                text("SELECT revision_id FROM revision_heads WHERE entity_id=:id"),
                {"id": identifier},
            ).scalars()
        )
        if heads != [chain[-1].revision_id]:
            raise VaultError("Неверная принятая ветвь периода.")
        return chain

    def history(self, month: AccountingMonth) -> list[PeriodRevision]:
        if not isinstance(month, AccountingMonth):
            raise ValidationError("Укажите календарный месяц.")
        with self._mutex, self._engine.connect() as connection:
            return self._history(connection, month)

    def _insert_payload(
        self,
        connection: Connection,
        revision: str,
        month: AccountingMonth,
        is_closed: bool,
        reason: str,
    ) -> None:
        connection.execute(
            text("""INSERT INTO period_revisions(revision_id,month,is_closed,reason)
            VALUES (:revision,:month,:closed,:reason)"""),
            {"revision": revision, "month": month.key, "closed": int(is_closed), "reason": reason},
        )

    def set_state(
        self,
        month: AccountingMonth,
        is_closed: bool,
        *,
        expected_revision_id: str | None,
        reason: str,
        command_id: str | None,
    ) -> PeriodRevision:
        reason = validate_period_change(month, is_closed, reason)
        command_id = command_id or str(uuid4())
        require_identifier(command_id)
        if expected_revision_id is not None:
            require_identifier(expected_revision_id)
        digest = hashlib.sha256(
            canonical_json(
                {
                    "kind": "period-command",
                    "month": month.key,
                    "is_closed": is_closed,
                    "reason": reason,
                    "expected_revision_id": expected_revision_id,
                }
            )
        ).hexdigest()
        identifier = period_id(self._book_id, month)
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
            history = self._history(connection, month)
            current = history[-1] if history else None
            if (current.revision_id if current else None) != expected_revision_id:
                raise ValidationError(
                    "Состояние периода изменено. Обновите список и повторите действие."
                )
            if (current.is_closed if current else False) == is_closed:
                raise ValidationError("Период уже закрыт." if is_closed else "Период уже открыт.")
            if current is None:
                connection.execute(
                    text("INSERT INTO entities(id,book_id,kind) VALUES (:id,:book,'period')"),
                    {"id": identifier, "book": self._book_id},
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
                (id,entity_id,replica_id,sequence,group_id,created_at,content_hash)
                VALUES (:id,:entity,:replica,:sequence,:group_id,:created,:hash)"""),
                {
                    "id": revision,
                    "entity": identifier,
                    "replica": self._replica_id,
                    "sequence": sequence,
                    "group_id": group,
                    "created": created,
                    "hash": period_hash(month, is_closed, reason, expected_revision_id),
                },
            )
            self._insert_payload(connection, revision, month, is_closed, reason)
            if expected_revision_id:
                connection.execute(
                    text(
                        "INSERT INTO revision_parents(revision_id,parent_id) "
                        "VALUES (:revision,:parent)"
                    ),
                    {"revision": revision, "parent": expected_revision_id},
                )
            connection.execute(
                text("UPDATE entity_revisions SET sealed=1 WHERE id=:id"), {"id": revision}
            )
            connection.execute(
                text("UPDATE entities SET accepted_revision_id=:revision WHERE id=:id"),
                {"revision": revision, "id": identifier},
            )
            connection.execute(
                text("DELETE FROM revision_heads WHERE entity_id=:id"), {"id": identifier}
            )
            connection.execute(
                text(
                    "INSERT INTO revision_heads(entity_id,revision_id) VALUES (:entity,:revision)"
                ),
                {"entity": identifier, "revision": revision},
            )
            connection.execute(
                text("""INSERT INTO change_log(id,replica_id,sequence,group_id,revision_id)
                VALUES (:id,:replica,:sequence,:group_id,:revision)"""),
                {
                    "id": str(uuid4()),
                    "replica": self._replica_id,
                    "sequence": sequence,
                    "group_id": group,
                    "revision": revision,
                },
            )
            connection.execute(
                text(
                    "INSERT INTO command_receipts(id,command_hash,entity_id,revision_id) "
                    "VALUES (:id,:hash,:entity,:revision)"
                ),
                {"id": command_id, "hash": digest, "entity": identifier, "revision": revision},
            )
            return PeriodRevision(identifier, revision, month, is_closed, reason, created)

    def check_integrity(self) -> None:
        with self._mutex, self._engine.connect() as connection:
            try:
                rows = list(
                    connection.execute(text(self._select()), {"book": self._book_id}).mappings()
                )
                months = {self._record(row).month for row in rows}
                for month in months:
                    self._history(connection, month)
            except (ValidationError, ValueError, TypeError, AttributeError) as exc:
                raise VaultError("Проверка закрытых периодов не пройдена.") from exc
