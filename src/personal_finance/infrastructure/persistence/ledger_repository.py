"""SQL-адаптер финансового журнала с атомарными неизменяемыми версиями."""

import hashlib
from datetime import date
from threading import RLock
from typing import Any
from uuid import UUID, uuid4, uuid5

from sqlalchemy import Connection, Engine, text
from sqlalchemy.engine import RowMapping

from personal_finance.application.catalog import utc_now
from personal_finance.domain.errors import ValidationError, VaultError
from personal_finance.domain.ledger import (
    AccountBalance,
    LedgerRole,
    Operation,
    OperationDraft,
    OperationKind,
    OperationPart,
    require_identifier,
)
from personal_finance.domain.models import Currency, Money
from personal_finance.domain.query import OperationQuery
from personal_finance.domain.reconciliation import Reconciliation, reconcile_snapshot
from personal_finance.domain.refunds import (
    RefundCandidate,
    calculate_refund_candidates,
    validate_refund_reference,
)
from personal_finance.infrastructure.persistence.period_repository import require_open_dates
from personal_finance.infrastructure.security.keys import canonical_json

COLUMNS = (
    "kind",
    "occurred_on",
    "account_id",
    "amount_minor",
    "currency",
    "category_id",
    "destination_account_id",
    "fee_minor",
    "fee_category_id",
    "note",
)
SELECT_COLUMNS = ",".join("p." + column for column in COLUMNS)


def operation_payload(draft: OperationDraft) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "kind": draft.kind.value,
        "occurred_on": draft.occurred_on.isoformat(),
        "account_id": draft.account_id,
        "amount_minor": draft.amount.minor,
        "currency": draft.amount.currency.code,
        "category_id": draft.category_id,
        "destination_account_id": draft.destination_account_id,
        "fee_minor": draft.fee.minor,
        "fee_category_id": draft.fee_category_id,
        "note": draft.note,
    }
    # Пустые части не меняют хеши и квитанции опубликованной схемы 0003.
    if draft.parts:
        payload["parts"] = [
            {"category_id": part.category_id, "amount_minor": part.amount.minor}
            for part in draft.parts
        ]
    if draft.refund_of is not None:
        payload["refund_of"] = draft.refund_of
        payload["refund_of_revision_id"] = draft.refund_of_revision_id
    if draft.tag_ids:
        payload["tag_ids"] = list(draft.tag_ids)
    return payload


def revision_hash(
    draft: OperationDraft,
    is_deleted: bool,
    parent_revision_id: str | None = None,
) -> str:
    return hashlib.sha256(
        canonical_json(
            {
                "kind": "operation",
                "payload": operation_payload(draft),
                "is_deleted": is_deleted,
                "parent_revision_id": parent_revision_id,
            }
        )
    ).hexdigest()


class SqlLedgerRepository:
    def __init__(
        self,
        engine: Engine,
        book_id: str,
        replica_id: str,
        mutex: RLock,
        *,
        supports_splits: bool = True,
        supports_refunds: bool = False,
        supports_periods: bool = False,
        supports_tags: bool = False,
    ) -> None:
        self._engine = engine
        self._book_id = book_id
        self._replica_id = replica_id
        self._mutex = mutex
        self.supports_splits = supports_splits
        self.supports_refunds = supports_refunds
        self._supports_periods = supports_periods
        self.supports_tags = supports_tags

    def _record(self, connection: Connection, row: RowMapping) -> Operation:
        currency = Currency(row["currency"], row["exponent"])
        rows = (
            list(
                connection.execute(
                    text(
                        "SELECT position,category_id,amount_minor FROM operation_parts "
                        "WHERE revision_id=:revision ORDER BY position"
                    ),
                    {"revision": row["revision_id"]},
                ).mappings()
            )
            if self.supports_splits
            else []
        )
        if any(part["position"] != index for index, part in enumerate(rows)):
            raise VaultError("Нарушена последовательность частей операции.")
        parts = tuple(
            OperationPart(part["category_id"], Money(part["amount_minor"], currency))
            for part in rows
        )
        return Operation(
            row["id"],
            row["revision_id"],
            OperationDraft(
                OperationKind(row["kind"]),
                date.fromisoformat(row["occurred_on"]),
                row["account_id"],
                Money(row["amount_minor"], currency),
                row["category_id"],
                row["destination_account_id"],
                Money(row["fee_minor"], currency),
                row["fee_category_id"],
                row["note"],
                parts,
                row["refund_of"],
                row["refund_of_revision_id"],
                tuple(
                    connection.execute(
                        text(
                            "SELECT tag_id FROM operation_tags WHERE revision_id=:revision "
                            "ORDER BY tag_id"
                        ),
                        {"revision": row["revision_id"]},
                    ).scalars()
                )
                if self.supports_tags
                else (),
            ),
            bool(row["is_deleted"]),
        )

    def _select(self) -> str:
        links = (
            "p.refund_of,p.refund_of_revision_id"
            if self.supports_refunds
            else "NULL AS refund_of,NULL AS refund_of_revision_id"
        )
        return f"""SELECT e.id,r.id AS revision_id,r.is_deleted,{SELECT_COLUMNS},c.exponent,{links}
            FROM entities e JOIN entity_revisions r ON r.entity_id=e.id
            LEFT JOIN operation_revisions p ON p.revision_id=r.id
            LEFT JOIN currencies c ON c.code=p.currency
            WHERE e.book_id=:book AND e.kind='operation'"""

    def _revision(self, connection: Connection, revision_id: str) -> Operation:
        row = (
            connection.execute(
                text(self._select() + " AND r.id=:revision"),
                {"book": self._book_id, "revision": revision_id},
            )
            .mappings()
            .first()
        )
        if row is None:
            raise ValidationError("Финансовая операция не найдена в этой книге.")
        return self._record(connection, row)

    def operation(self, operation_id: str) -> Operation:
        require_identifier(operation_id)
        with self._mutex, self._engine.connect() as connection:
            row = (
                connection.execute(
                    text(self._select() + " AND e.id=:id AND r.id=e.accepted_revision_id"),
                    {"book": self._book_id, "id": operation_id},
                )
                .mappings()
                .first()
            )
            if row is None:
                raise ValidationError("Финансовая операция не найдена в этой книге.")
            return self._record(connection, row)

    def operations(self, *, include_deleted: bool = False) -> list[Operation]:
        with self._mutex, self._engine.connect() as connection:
            rows = connection.execute(
                text(
                    self._select()
                    + """ AND r.id=e.accepted_revision_id AND (:deleted=1 OR r.is_deleted=0)
                    ORDER BY p.occurred_on DESC,r.created_at DESC,e.id"""
                ),
                {"book": self._book_id, "deleted": int(include_deleted)},
            ).mappings()
            return [self._record(connection, row) for row in rows]

    def _history(self, connection: Connection, operation_id: str) -> list[Operation]:
        rows = connection.execute(
            text(self._select() + " AND e.id=:id"),
            {"book": self._book_id, "id": operation_id},
        ).mappings()
        records = {row["revision_id"]: self._record(connection, row) for row in rows}
        if not records:
            raise ValidationError("История операции не найдена в этой книге.")
        parents: dict[str, list[str]] = {revision: [] for revision in records}
        links = connection.execute(
            text("""SELECT p.revision_id,p.parent_id
            FROM revision_parents p JOIN entity_revisions r ON r.id=p.revision_id
            JOIN entities e ON e.id=r.entity_id WHERE e.id=:id AND e.book_id=:book"""),
            {"id": operation_id, "book": self._book_id},
        )
        for link in links.mappings():
            parents[link["revision_id"]].append(link["parent_id"])
        current: str | None = connection.execute(
            text("SELECT accepted_revision_id FROM entities WHERE id=:id"),
            {"id": operation_id},
        ).scalar_one()
        chain = []
        visited = set()
        while current is not None:
            if current not in records or current in visited or len(parents[current]) > 1:
                raise VaultError("Нарушена последовательность финансовых версий.")
            visited.add(current)
            chain.append(records[current])
            current = parents[current][0] if parents[current] else None
        if len(visited) != len(records):
            raise VaultError("В истории найдены непринятые финансовые ветви.")
        return list(reversed(chain))

    def history(self, operation_id: str) -> list[Operation]:
        require_identifier(operation_id)
        with self._mutex, self._engine.connect() as connection:
            return self._history(connection, operation_id)

    def _current_operations(self, connection: Connection) -> list[Operation]:
        rows = connection.execute(
            text(
                self._select() + " AND r.id=e.accepted_revision_id "
                "ORDER BY p.occurred_on DESC,r.created_at DESC,e.id"
            ),
            {"book": self._book_id},
        ).mappings()
        return [self._record(connection, row) for row in rows]

    def refund_candidates(
        self,
        *,
        excluding_refund_id: str | None = None,
    ) -> list[RefundCandidate]:
        if not self.supports_refunds:
            raise ValidationError("Для возвратов создайте копию и восстановите её в новую папку.")
        if excluding_refund_id is not None:
            require_identifier(excluding_refund_id)
        with self._mutex, self._engine.connect() as connection:
            return calculate_refund_candidates(
                self._current_operations(connection),
                excluding_refund_id=excluding_refund_id,
            )

    def _require_refund_source(
        self,
        connection: Connection,
        draft: OperationDraft,
        *,
        current: bool,
    ) -> None:
        if draft.kind != OperationKind.REFUND:
            return
        if not self.supports_refunds:
            raise ValidationError("Для возвратов создайте копию и восстановите её в новую папку.")
        source = self._revision(connection, draft.refund_of_revision_id or "")
        validate_refund_reference(draft, source)
        if current:
            accepted = connection.execute(
                text("SELECT accepted_revision_id FROM entities WHERE id=:id AND book_id=:book"),
                {"id": source.id, "book": self._book_id},
            ).scalar_one_or_none()
            if accepted != source.revision_id:
                raise ValidationError(
                    "Исходный расход изменён. Обновите список и повторите возврат."
                )

    def search(self, query: OperationQuery) -> list[Operation]:
        if not isinstance(query, OperationQuery):
            raise ValidationError("Некорректный фильтр журнала.")
        if query.tag_ids and not self.supports_tags:
            raise ValidationError("Для тегов создайте копию и восстановите её в новую папку.")
        with self._mutex, self._engine.connect() as connection:
            institutions: dict[str, str | None] = {
                row["id"]: row["institution_id"]
                for row in connection.execute(
                    text("""SELECT e.id,a.institution_id
                    FROM entities e JOIN account_revisions a ON a.revision_id=e.accepted_revision_id
                    WHERE e.book_id=:book AND e.kind='account'"""),
                    {"book": self._book_id},
                ).mappings()
            }
            return [
                value
                for value in self._current_operations(connection)
                if query.matches(value, institutions)
            ]

    def _require_tags(
        self,
        connection: Connection,
        tag_ids: tuple[str, ...],
        *,
        previous_revision_id: str | None = None,
        require_active: bool = True,
    ) -> None:
        if not self.supports_tags:
            if tag_ids:
                raise ValidationError("Теги не поддерживаются схемой этой книги.")
            return
        previous: set[str] = (
            set(
                connection.execute(
                    text("SELECT tag_id FROM operation_tags WHERE revision_id=:revision"),
                    {"revision": previous_revision_id},
                ).scalars()
            )
            if previous_revision_id
            else set()
        )
        for identifier in tag_ids:
            valid = connection.execute(
                text("""SELECT 1 FROM entities e JOIN entity_revisions r
                ON r.id=e.accepted_revision_id JOIN tag_revisions p ON p.revision_id=r.id
                WHERE e.id=:id AND e.book_id=:book AND e.kind='tag' AND r.sealed=1
                AND (:active=0 OR r.is_deleted=0)"""),
                {
                    "id": identifier,
                    "book": self._book_id,
                    "active": int(require_active and identifier not in previous),
                },
            ).first()
            if valid is None:
                raise ValidationError("Тег не найден в этой книге или находится в архиве.")

    def _insert_tags(self, connection: Connection, revision: str, tag_ids: tuple[str, ...]) -> None:
        if tag_ids:
            connection.execute(
                text("INSERT INTO operation_tags(revision_id,tag_id) VALUES (:revision,:tag)"),
                [{"revision": revision, "tag": identifier} for identifier in tag_ids],
            )

    def _require_references(
        self,
        connection: Connection,
        draft: OperationDraft,
        *,
        require_active: bool = True,
    ) -> None:
        for posting in draft.postings():
            if posting.role == LedgerRole.OPENING:
                continue
            if posting.role == LedgerRole.ASSET:
                valid = connection.execute(
                    text("""SELECT 1 FROM entities e
                    JOIN entity_revisions r ON r.id=e.accepted_revision_id
                    JOIN account_revisions p ON p.revision_id=r.id
                    WHERE e.id=:id AND e.book_id=:book AND e.kind='account'
                    AND (:active=0 OR r.is_deleted=0) AND p.currency=:currency"""),
                    {
                        "id": posting.reference_id,
                        "book": self._book_id,
                        "active": int(require_active),
                        "currency": posting.amount.currency.code,
                    },
                ).first()
                if not valid:
                    raise ValidationError("Счёт не найден в этой книге или валюта не совпадает.")
            else:
                valid = connection.execute(
                    text("""SELECT 1 FROM entities e
                    JOIN entity_revisions r ON r.id=e.accepted_revision_id
                    JOIN category_revisions p ON p.revision_id=r.id
                    WHERE e.id=:id AND e.book_id=:book AND e.kind='category'
                    AND (:active=0 OR r.is_deleted=0) AND p.kind=:kind"""),
                    {
                        "id": posting.reference_id,
                        "book": self._book_id,
                        "active": int(require_active),
                        "kind": posting.role.value,
                    },
                ).first()
                if not valid:
                    raise ValidationError("Выберите категорию нужного типа из этой книги.")
        currency = connection.execute(
            text("SELECT exponent FROM currencies WHERE code=:currency"),
            {"currency": draft.amount.currency.code},
        ).scalar_one_or_none()
        if currency != draft.amount.currency.exponent:
            raise ValidationError("Точность валюты не соответствует справочнику книги.")

    def _ledger_id(self, role: LedgerRole, reference_id: str | None, currency: str) -> str:
        return str(
            uuid5(UUID(self._book_id), f"ledger/{role.value}/{reference_id or ''}/{currency}")
        )

    def _insert_postings(
        self,
        connection: Connection,
        revision_id: str,
        draft: OperationDraft,
    ) -> None:
        for position, posting in enumerate(draft.postings()):
            ledger_id = self._ledger_id(
                posting.role,
                posting.reference_id,
                posting.amount.currency.code,
            )
            connection.execute(
                text("""INSERT OR IGNORE INTO ledger_accounts
                (id,book_id,role,reference_id,currency)
                VALUES (:id,:book,:role,:reference,:currency)"""),
                {
                    "id": ledger_id,
                    "book": self._book_id,
                    "role": posting.role.value,
                    "reference": posting.reference_id,
                    "currency": posting.amount.currency.code,
                },
            )
            connection.execute(
                text("""INSERT INTO postings
                (revision_id,position,ledger_account_id,amount_minor)
                VALUES (:revision,:position,:account,:minor)"""),
                {
                    "revision": revision_id,
                    "position": position,
                    "account": ledger_id,
                    "minor": posting.amount.minor,
                },
            )

    def _balances(self, connection: Connection, as_of: date | None) -> list[AccountBalance]:
        accounts = list(
            connection.execute(
                text("""SELECT e.id,a.currency,c.exponent
            FROM entities e JOIN entity_revisions r ON r.id=e.accepted_revision_id
            JOIN account_revisions a ON a.revision_id=r.id JOIN currencies c ON c.code=a.currency
            WHERE e.book_id=:book AND e.kind='account' AND r.is_deleted=0 ORDER BY e.id"""),
                {"book": self._book_id},
            ).mappings()
        )
        totals: dict[str, int] = {account["id"]: 0 for account in accounts}
        rows = connection.execute(
            text("""SELECT l.reference_id,p.amount_minor
            FROM entities e JOIN entity_revisions r ON r.id=e.accepted_revision_id
            JOIN operation_revisions o ON o.revision_id=r.id
            JOIN postings p ON p.revision_id=r.id
            JOIN ledger_accounts l ON l.id=p.ledger_account_id
            WHERE e.book_id=:book AND e.kind='operation' AND r.is_deleted=0
            AND l.role='asset' AND (:as_of IS NULL OR o.occurred_on<=:as_of)"""),
            {"book": self._book_id, "as_of": as_of.isoformat() if as_of else None},
        )
        for row in rows.mappings():
            account_id, minor = row["reference_id"], row["amount_minor"]
            if account_id in totals:
                totals[account_id] += minor
        return [
            AccountBalance(
                account["id"],
                Money(totals[account["id"]], Currency(account["currency"], account["exponent"])),
            )
            for account in accounts
        ]

    def balances(self, as_of: date | None = None) -> list[AccountBalance]:
        if as_of is not None and type(as_of) is not date:
            raise ValidationError("Укажите календарную дату остатка.")
        with self._mutex, self._engine.connect() as connection:
            return self._balances(connection, as_of)

    def reconcile(self, account_id: str, as_of: date, statement_balance: Money) -> Reconciliation:
        require_identifier(account_id)
        if type(as_of) is not date or not isinstance(statement_balance, Money):
            raise ValidationError("Укажите календарную дату и точный остаток выписки.")
        with self._mutex, self._engine.connect() as connection:
            balance = next(
                (
                    item.amount
                    for item in self._balances(connection, as_of)
                    if item.account_id == account_id
                ),
                None,
            )
            if balance is None:
                raise ValidationError("Счёт для сверки не найден в этой книге.")
            return reconcile_snapshot(
                account_id, as_of, balance, statement_balance, self._current_operations(connection)
            )

    def _validate_timeline(self, connection: Connection) -> None:
        rows = connection.execute(
            text("""SELECT o.occurred_on,l.reference_id,l.currency,
            c.exponent,p.amount_minor FROM entities e
            JOIN entity_revisions r ON r.id=e.accepted_revision_id
            JOIN operation_revisions o ON o.revision_id=r.id JOIN postings p ON p.revision_id=r.id
            JOIN ledger_accounts l ON l.id=p.ledger_account_id
            JOIN currencies c ON c.code=l.currency
            WHERE e.book_id=:book AND e.kind='operation' AND r.is_deleted=0 AND l.role='asset'"""),
            {"book": self._book_id},
        )
        deltas: dict[tuple[str, str], int] = {}
        currencies: dict[str, Currency] = {}
        for row in rows.mappings():
            key = (row["occurred_on"], row["reference_id"])
            deltas[key] = deltas.get(key, 0) + row["amount_minor"]
            currencies[row["reference_id"]] = Currency(row["currency"], row["exponent"])
        totals: dict[str, int] = {}
        for (_, account_id), delta in sorted(deltas.items()):
            totals[account_id] = totals.get(account_id, 0) + delta
            Money(totals[account_id], currencies[account_id])

    def _validate_state(self, connection: Connection) -> None:
        self._balances(connection, None)
        self._validate_timeline(connection)
        rows: list[Any] = list(
            connection.execute(
                text("""SELECT o.kind,o.occurred_on,l.reference_id
            FROM entities e JOIN entity_revisions r ON r.id=e.accepted_revision_id
            JOIN operation_revisions o ON o.revision_id=r.id JOIN postings p ON p.revision_id=r.id
            JOIN ledger_accounts l ON l.id=p.ledger_account_id
            WHERE e.book_id=:book AND e.kind='operation' AND r.is_deleted=0 AND l.role='asset'"""),
                {"book": self._book_id},
            )
        )
        opening_dates: dict[str, str] = {}
        for kind, occurred_on, account_id in rows:
            if kind == OperationKind.OPENING:
                if account_id in opening_dates:
                    raise ValidationError(
                        "На счёте уже есть начальный остаток. Исправьте существующий."
                    )
                opening_dates[account_id] = occurred_on
        for kind, occurred_on, account_id in rows:
            if kind != OperationKind.OPENING and occurred_on < opening_dates.get(account_id, ""):
                raise ValidationError("Дата операции раньше начального остатка этого счёта.")

        if self.supports_refunds:
            calculate_refund_candidates(self._current_operations(connection))

    def save(
        self,
        draft: OperationDraft,
        *,
        operation_id: str | None = None,
        expected_revision_id: str | None = None,
        is_deleted: bool = False,
        command_id: str | None = None,
    ) -> Operation:
        if not isinstance(draft, OperationDraft) or type(is_deleted) is not bool:
            raise ValidationError("Некорректное действие с финансовой операцией.")
        if draft.tag_ids and not self.supports_tags:
            raise ValidationError("Для тегов создайте копию и восстановите её в новую папку.")
        if draft.kind == OperationKind.REFUND and not self.supports_refunds:
            raise ValidationError("Для возвратов создайте копию и восстановите её в новую папку.")
        if draft.parts and not self.supports_splits:
            raise ValidationError("Для разделения создайте копию и восстановите её в новую папку.")
        command_id = command_id or str(uuid4())
        require_identifier(command_id)
        if operation_id is None:
            if expected_revision_id is not None or is_deleted:
                raise ValidationError("Сначала создайте финансовую операцию.")
        else:
            require_identifier(operation_id)
            if expected_revision_id is None:
                raise ValidationError("Для исправления нужна текущая версия операции.")
            require_identifier(expected_revision_id)
        digest = hashlib.sha256(
            canonical_json(
                {
                    "kind": "operation",
                    "payload": operation_payload(draft),
                    "operation_id": operation_id,
                    "expected_revision_id": expected_revision_id,
                    "is_deleted": is_deleted,
                }
            )
        ).hexdigest()
        with self._mutex, self._engine.begin() as connection:
            receipt = (
                connection.execute(
                    text("""SELECT command_hash,revision_id
                FROM command_receipts WHERE id=:id"""),
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
            if self._supports_periods:
                dates = [draft.occurred_on]
                if operation_id is not None:
                    row = (
                        connection.execute(
                            text(self._select() + " AND e.id=:id AND r.id=e.accepted_revision_id"),
                            {"book": self._book_id, "id": operation_id},
                        )
                        .mappings()
                        .first()
                    )
                    if row is not None:
                        dates.append(date.fromisoformat(row["occurred_on"]))
                require_open_dates(connection, self._book_id, *dates)
            self._require_references(connection, draft, require_active=not is_deleted)
            self._require_refund_source(connection, draft, current=not is_deleted)
            if operation_id is not None:
                current = (
                    connection.execute(
                        text("""SELECT e.accepted_revision_id,r.is_deleted
                    FROM entities e JOIN entity_revisions r ON r.id=e.accepted_revision_id
                    WHERE e.id=:id AND e.book_id=:book AND e.kind='operation'"""),
                        {"id": operation_id, "book": self._book_id},
                    )
                    .mappings()
                    .first()
                )
                if not current or current["accepted_revision_id"] != expected_revision_id:
                    raise ValidationError(
                        "Операция уже изменена. Обновите журнал и повторите действие."
                    )
                if current["is_deleted"]:
                    raise ValidationError("Операция уже удалена; её история сохранена.")
            else:
                operation_id = str(uuid4())
                connection.execute(
                    text("INSERT INTO entities(id,book_id,kind) VALUES (:id,:book,'operation')"),
                    {"id": operation_id, "book": self._book_id},
                )
            self._require_tags(
                connection,
                draft.tag_ids,
                previous_revision_id=expected_revision_id,
                require_active=not is_deleted,
            )
            revision_id, group_id = str(uuid4()), str(uuid4())
            sequence: int = connection.execute(
                text("""UPDATE replicas SET sequence=sequence+1
                WHERE id=:id AND book_id=:book RETURNING sequence"""),
                {"id": self._replica_id, "book": self._book_id},
            ).scalar_one()
            created = utc_now()
            connection.execute(
                text(
                    "INSERT INTO change_groups(id,book_id,created_at) VALUES (:id,:book,:created)"
                ),
                {"id": group_id, "book": self._book_id, "created": created},
            )
            connection.execute(
                text("""INSERT INTO entity_revisions
                (id,entity_id,replica_id,sequence,group_id,created_at,content_hash,is_deleted)
                VALUES (:id,:entity,:replica,:sequence,:group_id,:created,:hash,:deleted)"""),
                {
                    "id": revision_id,
                    "entity": operation_id,
                    "replica": self._replica_id,
                    "sequence": sequence,
                    "group_id": group_id,
                    "created": created,
                    "hash": revision_hash(draft, is_deleted, expected_revision_id),
                    "deleted": int(is_deleted),
                },
            )
            typed_columns = COLUMNS + (
                ("refund_of", "refund_of_revision_id") if self.supports_refunds else ()
            )
            columns = ",".join(("revision_id", *typed_columns))
            values = ",".join(":" + column for column in ("revision_id", *typed_columns))
            payload = operation_payload(draft)
            connection.execute(
                text(f"INSERT INTO operation_revisions ({columns}) VALUES ({values})"),
                {"revision_id": revision_id, **{key: payload.get(key) for key in typed_columns}},
            )
            if draft.parts:
                connection.execute(
                    text(
                        "INSERT INTO operation_parts "
                        "(revision_id,position,category_id,amount_minor) "
                        "VALUES (:revision,:position,:category,:amount)"
                    ),
                    [
                        {
                            "revision": revision_id,
                            "position": index,
                            "category": part.category_id,
                            "amount": part.amount.minor,
                        }
                        for index, part in enumerate(draft.parts)
                    ],
                )
            self._insert_postings(connection, revision_id, draft)
            self._insert_tags(connection, revision_id, draft.tag_ids)
            if expected_revision_id:
                connection.execute(
                    text(
                        "INSERT INTO revision_parents(revision_id,parent_id) "
                        "VALUES (:revision,:parent)"
                    ),
                    {"revision": revision_id, "parent": expected_revision_id},
                )
            connection.execute(
                text("UPDATE entity_revisions SET sealed=1 WHERE id=:id"), {"id": revision_id}
            )
            connection.execute(
                text("UPDATE entities SET accepted_revision_id=:revision WHERE id=:id"),
                {"revision": revision_id, "id": operation_id},
            )
            self._validate_state(connection)
            connection.execute(
                text("DELETE FROM revision_heads WHERE entity_id=:entity"), {"entity": operation_id}
            )
            connection.execute(
                text(
                    "INSERT INTO revision_heads(entity_id,revision_id) VALUES (:entity,:revision)"
                ),
                {"entity": operation_id, "revision": revision_id},
            )
            connection.execute(
                text("""INSERT INTO change_log(id,replica_id,sequence,group_id,revision_id)
                VALUES (:id,:replica,:sequence,:group_id,:revision)"""),
                {
                    "id": str(uuid4()),
                    "replica": self._replica_id,
                    "sequence": sequence,
                    "group_id": group_id,
                    "revision": revision_id,
                },
            )
            connection.execute(
                text("""INSERT INTO command_receipts(id,command_hash,entity_id,revision_id)
                VALUES (:id,:hash,:entity,:revision)"""),
                {"id": command_id, "hash": digest, "entity": operation_id, "revision": revision_id},
            )
            return Operation(operation_id, revision_id, draft, is_deleted)

    def check_integrity(self) -> None:
        with self._mutex, self._engine.connect() as connection:
            rows = connection.execute(
                text(self._select() + " ORDER BY r.id"),
                {"book": self._book_id},
            ).mappings()
            checked_entities: set[str] = set()
            try:
                for row in rows:
                    operation = self._record(connection, row)
                    if operation.id not in checked_entities:
                        self._history(connection, operation.id)
                        checked_entities.add(operation.id)
                    stored_hash: str = connection.execute(
                        text("SELECT content_hash FROM entity_revisions WHERE id=:id"),
                        {"id": operation.revision_id},
                    ).scalar_one()
                    parents: list[str] = list(
                        connection.execute(
                            text("SELECT parent_id FROM revision_parents WHERE revision_id=:id"),
                            {"id": operation.revision_id},
                        ).scalars()
                    )
                    if len(parents) > 1:
                        raise VaultError("Нарушена последовательность финансовых версий.")
                    parent = parents[0] if parents else None
                    if stored_hash != revision_hash(operation.draft, operation.is_deleted, parent):
                        raise VaultError("Содержимое финансовой версии не соответствует хешу.")
                    self._require_references(connection, operation.draft, require_active=False)
                    self._require_refund_source(connection, operation.draft, current=False)
                    self._require_tags(connection, operation.draft.tag_ids, require_active=False)
                    stored = list(
                        connection.execute(
                            text("""SELECT p.position,l.id,l.role,l.reference_id,
                        l.currency,p.amount_minor,l.book_id FROM postings p JOIN ledger_accounts l
                        ON l.id=p.ledger_account_id WHERE p.revision_id=:revision
                        ORDER BY p.position"""),
                            {"revision": operation.revision_id},
                        )
                    )
                    expected = [
                        (
                            position,
                            self._ledger_id(
                                posting.role, posting.reference_id, posting.amount.currency.code
                            ),
                            posting.role.value,
                            posting.reference_id,
                            posting.amount.currency.code,
                            posting.amount.minor,
                            self._book_id,
                        )
                        for position, posting in enumerate(operation.draft.postings())
                    ]
                    if [tuple(posting) for posting in stored] != expected:
                        raise VaultError("Проводки не соответствуют финансовой версии.")
                self._validate_state(connection)
            except (ValidationError, ValueError, TypeError) as exc:
                raise VaultError("Проверка финансового журнала не пройдена.") from exc
