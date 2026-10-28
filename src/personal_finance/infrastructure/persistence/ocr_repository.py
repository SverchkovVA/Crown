"""Атомарная OCR-запись: существующий Ledger и неизменяемое происхождение."""

from threading import RLock
from uuid import UUID, uuid4, uuid5

from sqlalchemy import Connection, Engine, text
from sqlalchemy.exc import DatabaseError, IntegrityError

from personal_finance.application.catalog import utc_now
from personal_finance.domain.errors import ValidationError, VaultError
from personal_finance.domain.imports import account_movement, digest_json
from personal_finance.domain.ledger import OperationKind, require_identifier
from personal_finance.domain.ocr_imports import (
    OcrEvidence,
    OcrImportPreview,
    OcrImportResult,
    OcrImportStatus,
    OcrSelection,
)
from personal_finance.infrastructure.persistence.ledger_repository import (
    SqlLedgerRepository,
    operation_payload,
)


class SqlOcrRepository:
    def __init__(
        self, engine: Engine, book_id: str, mutex: RLock, ledger: SqlLedgerRepository
    ) -> None:
        self._engine, self._book_id, self._mutex, self._ledger = engine, book_id, mutex, ledger

    def _request(self, selection: OcrSelection) -> str:
        if not isinstance(selection, OcrSelection):
            raise ValidationError("Некорректная финансовая команда OCR.")
        return digest_json(
            {
                "kind": "ocr-confirm-v1",
                "evidence": selection.evidence.data,
                "account_id": selection.account_id,
                "draft": operation_payload(selection.draft),
                "acknowledged_duplicate": selection.acknowledged_duplicate,
                "existing_operation_id": selection.existing_operation_id,
                "expected_revision_id": selection.expected_revision_id,
            }
        )

    def _account(self, connection: Connection, identifier: str, *, active: bool = True) -> None:
        if (
            connection.execute(
                text("""SELECT 1 FROM entities e JOIN entity_revisions r
            ON r.id=e.accepted_revision_id JOIN account_revisions p ON p.revision_id=r.id
            WHERE e.id=:id AND e.book_id=:book AND e.kind='account' AND r.sealed=1
            AND (:active=0 OR r.is_deleted=0) AND p.currency='RUB'"""),
                {"id": identifier, "book": self._book_id, "active": int(active)},
            ).first()
            is None
        ):
            raise ValidationError("Выберите активный счёт RUB из этой книги.")

    def _preview(self, connection: Connection, selection: OcrSelection) -> OcrImportPreview:
        self._account(connection, selection.account_id)
        data = selection.evidence.data
        previous = list(
            connection.execute(
                text("SELECT * FROM ocr_records WHERE book_id=:book AND image_sha256=:image"),
                {"book": self._book_id, "image": data["image_sha256"]},
            ).mappings()
        )
        money = set(data["fields"]["amount"]["words"])
        for old in previous:
            old_evidence = OcrEvidence(old["evidence_json"])
            if old["document_sha256"] != data["document_sha256"] or old["bank"] != data["bank"]:
                return OcrImportPreview(
                    selection,
                    OcrImportStatus.CONFLICT,
                    message="Это изображение уже подтверждалось с другим OCR-контекстом.",
                )
            if old["source_key"] == selection.evidence.source_key:
                operation = self._ledger._revision(connection, old["revision_id"])
                if (
                    old_evidence.content_hash == selection.evidence.content_hash
                    and old["account_id"] == selection.account_id
                    and operation.draft == selection.draft
                ):
                    return OcrImportPreview(
                        selection,
                        OcrImportStatus.DUPLICATE,
                        (operation,),
                        "Эта строка изображения уже подтверждена.",
                    )
                return OcrImportPreview(
                    selection,
                    OcrImportStatus.CONFLICT,
                    (operation,),
                    "Изменены данные уже подтверждённой строки OCR.",
                )
            if money.intersection(old_evidence.data["fields"]["amount"]["words"]):
                return OcrImportPreview(
                    selection,
                    OcrImportStatus.CONFLICT,
                    message="Область суммы пересекается с уже подтверждённой строкой.",
                )
        delta = account_movement(selection.draft, selection.account_id)
        candidates = tuple(
            op
            for op in self._ledger._current_operations(connection)
            if not op.is_deleted
            and op.draft.kind != OperationKind.OPENING
            and op.draft.occurred_on == selection.draft.occurred_on
            and account_movement(op.draft, selection.account_id) == delta
        )
        return OcrImportPreview(
            selection,
            OcrImportStatus.POSSIBLE if candidates else OcrImportStatus.NEW,
            candidates,
            "Выберите связь или подтвердите отдельную операцию."
            if candidates
            else "Новое движение OCR после явного подтверждения.",
        )

    def preview(self, selection: OcrSelection) -> OcrImportPreview:
        self._request(selection)
        with self._mutex, self._engine.connect() as connection:
            return self._preview(connection, selection)

    def _result(self, connection: Connection, identifier: str) -> OcrImportResult:
        row = (
            connection.execute(
                text("SELECT * FROM ocr_records WHERE id=:id AND book_id=:book"),
                {"id": identifier, "book": self._book_id},
            )
            .mappings()
            .one()
        )
        return OcrImportResult(
            row["id"], self._ledger._revision(connection, row["revision_id"]), bool(row["linked"])
        )

    def confirm(self, selection: OcrSelection, command_id: str) -> OcrImportResult:
        digest = self._request(selection)
        require_identifier(command_id)
        try:
            with self._mutex, self._engine.begin() as connection:
                previous = (
                    connection.execute(
                        text(
                            "SELECT id,command_hash FROM ocr_records WHERE "
                            "command_id=:id AND book_id=:book"
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
                    raise ValidationError(
                        "Идентификатор действия уже использован для других данных."
                    )
                preview = self._preview(connection, selection)
                if preview.status in (OcrImportStatus.DUPLICATE, OcrImportStatus.CONFLICT):
                    raise ValidationError(
                        "Повторная или противоречивая строка OCR. Обновите предпросмотр."
                    )
                if (
                    preview.status == OcrImportStatus.POSSIBLE
                    and not selection.acknowledged_duplicate
                    and selection.existing_operation_id is None
                ):
                    raise ValidationError(
                        "Возможный повтор требует явного выбора связи или отдельной операции."
                    )
                identifier, group_id = str(uuid4()), str(uuid4())
                connection.execute(
                    text(
                        "INSERT INTO change_groups(id,book_id,created_at) VALUES "
                        "(:id,:book,:created)"
                    ),
                    {"id": group_id, "book": self._book_id, "created": utc_now()},
                )
                linked = selection.existing_operation_id is not None
                if linked:
                    operation = self._ledger._revision(
                        connection, selection.expected_revision_id or ""
                    )
                    current = connection.execute(
                        text(
                            "SELECT accepted_revision_id FROM entities WHERE id=:id AND "
                            "book_id=:book"
                        ),
                        {"id": selection.existing_operation_id, "book": self._book_id},
                    ).scalar_one_or_none()
                    if (
                        operation.id != selection.existing_operation_id
                        or operation.revision_id != current
                        or operation.is_deleted
                        or operation.draft != selection.draft
                        or all(op.revision_id != operation.revision_id for op in preview.candidates)
                    ):
                        raise ValidationError(
                            "Операция для связи изменилась или не соответствует движению OCR."
                        )
                else:
                    operation = self._ledger._save_in_transaction(
                        connection,
                        selection.draft,
                        command_id=str(uuid5(UUID(command_id), "ocr-operation-v1")),
                        group_id=group_id,
                    )
                connection.execute(
                    text(
                        "INSERT INTO "
                        "command_receipts(id,command_hash,entity_id,revision_id) "
                        "VALUES (:id,:hash,:entity,:revision)"
                    ),
                    {
                        "id": command_id,
                        "hash": digest,
                        "entity": operation.id,
                        "revision": operation.revision_id,
                    },
                )
                data = selection.evidence.data
                connection.execute(
                    text("""INSERT INTO ocr_records
                    (id,book_id,account_id,bank,file_sha256,image_sha256,document_sha256,
                    source_key,evidence_hash,evidence_json,operation_id,revision_id,group_id,
                    command_id,command_hash,linked,acknowledged_duplicate,created_at)
                    VALUES (:id,:book,:account,:bank,:file,:image,:document,:key,:evidence_hash,
                    :evidence,:operation,:revision,:group,:command,:digest,:linked,:ack,:created)"""),
                    {
                        "id": identifier,
                        "book": self._book_id,
                        "account": selection.account_id,
                        "bank": data["bank"],
                        "file": data["file_sha256"],
                        "image": data["image_sha256"],
                        "document": data["document_sha256"],
                        "key": selection.evidence.source_key,
                        "evidence_hash": selection.evidence.content_hash,
                        "evidence": selection.evidence.payload,
                        "operation": operation.id,
                        "revision": operation.revision_id,
                        "group": group_id,
                        "command": command_id,
                        "digest": digest,
                        "linked": int(linked),
                        "ack": int(selection.acknowledged_duplicate),
                        "created": utc_now(),
                    },
                )
                return self._result(connection, identifier)
        except IntegrityError:
            raise ValidationError(
                "Источник OCR уже записан или его связи недопустимы. Обновите просмотр."
            ) from None
        except DatabaseError:
            raise VaultError(
                "Не удалось завершить подтверждение OCR. Изменения отменены."
            ) from None

    def check_integrity(self) -> None:
        with self._mutex, self._engine.connect() as connection:
            try:
                contexts: dict[str, tuple[str, str, set[int]]] = {}
                movements: set[tuple[str, str, str]] = set()
                for row in connection.execute(
                    text("SELECT * FROM ocr_records WHERE book_id=:book"), {"book": self._book_id}
                ).mappings():
                    evidence = OcrEvidence(row["evidence_json"])
                    data = evidence.data
                    refs = set(data["fields"]["amount"]["words"])
                    known = contexts.get(data["image_sha256"])
                    if known is not None:
                        if known[:2] != (data["document_sha256"], data["bank"]) or known[
                            2
                        ].intersection(refs):
                            raise VaultError("Противоречивое происхождение строк изображения OCR.")
                        known[2].update(refs)
                    else:
                        contexts[data["image_sha256"]] = (
                            data["document_sha256"],
                            data["bank"],
                            refs,
                        )
                    movement = (data["image_sha256"], row["operation_id"], row["account_id"])
                    if (
                        movement in movements
                        or row["linked"] not in (0, 1)
                        or row["acknowledged_duplicate"] not in (0, 1)
                    ):
                        raise VaultError("Неверное решение о повторе OCR.")
                    movements.add(movement)
                    group_book = connection.execute(
                        text("SELECT book_id FROM change_groups WHERE id=:id"),
                        {"id": row["group_id"]},
                    ).scalar_one_or_none()
                    if group_book != self._book_id:
                        raise VaultError("Неверная книга атомарной группы OCR.")
                    operation = self._ledger._revision(connection, row["revision_id"])
                    self._account(connection, row["account_id"], active=False)
                    selection = OcrSelection(
                        evidence,
                        row["account_id"],
                        operation.draft,
                        bool(row["acknowledged_duplicate"]),
                        operation.id if row["linked"] else None,
                        operation.revision_id if row["linked"] else None,
                    )
                    for name in ("id", "command_id", "operation_id", "revision_id", "group_id"):
                        require_identifier(row[name])
                    if (
                        row["operation_id"] != operation.id
                        or operation.is_deleted
                        or row["source_key"] != evidence.source_key
                        or row["evidence_hash"] != evidence.content_hash
                        or row["command_hash"] != self._request(selection)
                    ):
                        raise VaultError("Некорректная запись происхождения OCR.")
                    if any(
                        row[col] != data[col]
                        for col in ("bank", "file_sha256", "image_sha256", "document_sha256")
                    ):
                        raise VaultError("Происхождение OCR не соответствует метаданным.")
                    receipt = connection.execute(
                        text(
                            "SELECT command_hash,entity_id,revision_id FROM "
                            "command_receipts WHERE id=:id"
                        ),
                        {"id": row["command_id"]},
                    ).one_or_none()
                    if receipt is None or tuple(receipt) != (
                        row["command_hash"],
                        operation.id,
                        operation.revision_id,
                    ):
                        raise VaultError("Отсутствует квитанция OCR.")
                    if not row["linked"]:
                        group: str = connection.execute(
                            text("SELECT group_id FROM entity_revisions WHERE id=:id"),
                            {"id": operation.revision_id},
                        ).scalar_one()
                        if group != row["group_id"]:
                            raise VaultError("Неверная атомарная группа OCR.")
            except (ValidationError, ValueError, TypeError, KeyError, AttributeError, IndexError):
                raise VaultError("Проверка истории OCR не пройдена.") from None
