"""Просмотр согласованной выборки и сохранение ровно этого снимка в новый файл."""

from collections.abc import Callable
from pathlib import Path
from threading import RLock
from typing import Protocol

from personal_finance.domain.errors import ValidationError
from personal_finance.domain.exporting import ExportFormat, ExportPreview, ExportSettings
from personal_finance.infrastructure.exporting.files import write_export_new


class ExportRepository(Protocol):
    def preview(self, settings: ExportSettings) -> ExportPreview: ...


class Exporting:
    def __init__(
        self,
        repository: ExportRepository,
        book_id: str,
        root: Path,
        mutex: RLock,
        ensure_open: Callable[[], None],
    ) -> None:
        self._repository, self._book_id, self._root = repository, book_id, root
        self._mutex, self._ensure_open = mutex, ensure_open

    def preview(self, settings: ExportSettings) -> ExportPreview:
        with self._mutex:
            self._ensure_open()
            return self._repository.preview(settings)

    def save(self, destination: Path, preview: ExportPreview, format: ExportFormat) -> Path:
        with self._mutex:
            self._ensure_open()
            if not isinstance(preview, ExportPreview) or preview.source_book_id != self._book_id:
                raise ValidationError("Предпросмотр экспорта относится к другой книге.")
            return write_export_new(destination, preview, format, vault_root=self._root)
