"""Подготовка и явное подтверждение локального импорта выписки."""

from pathlib import Path
from typing import Protocol
from uuid import uuid4

from personal_finance.domain.errors import ValidationError
from personal_finance.domain.imports import (
    MAX_IMPORT_BYTES,
    ImportDocument,
    ImportPreview,
    ImportResult,
    ImportSelection,
)
from personal_finance.infrastructure.importing.statements import (
    CsvMapping,
    csv_columns,
    parse_csv,
    parse_ofx,
)


class ImportRepository(Protocol):
    def preview(self, document: ImportDocument, account_id: str) -> ImportPreview: ...
    def confirm(
        self,
        document: ImportDocument,
        account_id: str,
        selections: tuple[ImportSelection, ...],
        command_id: str,
    ) -> ImportResult: ...
    def check_integrity(self) -> None: ...


def read_statement(path: Path) -> bytes:
    if path.suffix.lower() not in (".csv", ".ofx") or not path.is_file():
        raise ValidationError("Выберите обычный локальный файл CSV или OFX.")
    with path.open("rb") as source:
        contents = source.read(MAX_IMPORT_BYTES + 1)
    if not contents or len(contents) > MAX_IMPORT_BYTES:
        raise ValidationError("Файл должен быть непустым и не превышать 8 МиБ.")
    return contents


class Importing:
    def __init__(self, repository: ImportRepository) -> None:
        self._repository = repository

    @staticmethod
    def columns(contents: bytes, delimiter: str, encoding: str) -> tuple[str, ...]:
        return csv_columns(contents, delimiter, encoding)

    @staticmethod
    def parse_csv(contents: bytes, mapping: CsvMapping) -> ImportDocument:
        return parse_csv(contents, mapping)

    @staticmethod
    def parse_ofx(contents: bytes) -> ImportDocument:
        return parse_ofx(contents)

    def preview(self, document: ImportDocument, account_id: str) -> ImportPreview:
        return self._repository.preview(document, account_id)

    def confirm(
        self,
        document: ImportDocument,
        account_id: str,
        selections: tuple[ImportSelection, ...],
        *,
        command_id: str | None = None,
    ) -> ImportResult:
        return self._repository.confirm(
            document, account_id, selections, command_id or str(uuid4())
        )

    def check_integrity(self) -> None:
        self._repository.check_integrity()
