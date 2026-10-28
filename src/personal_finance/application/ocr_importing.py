"""Подготовка OCR-операции и отдельное явное финансовое подтверждение."""

from typing import Protocol
from uuid import uuid4

from personal_finance.application.ocr_cards import OcrCardPreview
from personal_finance.domain.errors import ValidationError
from personal_finance.domain.ledger import OperationDraft
from personal_finance.domain.ocr_cards import ReviewedOcrCard
from personal_finance.domain.ocr_imports import (
    OcrImportPreview,
    OcrImportResult,
    OcrSelection,
    make_ocr_evidence,
)


class OcrImportRepository(Protocol):
    def preview(self, selection: OcrSelection) -> OcrImportPreview: ...
    def confirm(self, selection: OcrSelection, command_id: str) -> OcrImportResult: ...
    def check_integrity(self) -> None: ...


class OcrImporting:
    def __init__(self, repository: OcrImportRepository) -> None:
        self._repository = repository

    @staticmethod
    def prepare(
        preview: OcrCardPreview,
        reviewed: ReviewedOcrCard,
        account_id: str,
        draft: OperationDraft,
    ) -> OcrSelection:
        if (
            not isinstance(preview, OcrCardPreview)
            or not isinstance(reviewed, ReviewedOcrCard)
            or preview.card != reviewed.source
        ):
            raise ValidationError("Проверенные поля относятся к другому источнику OCR.")
        return OcrSelection(
            make_ocr_evidence(preview.source.source_sha256, preview.bank_name, reviewed),
            account_id,
            draft,
        )

    def preview(self, selection: OcrSelection) -> OcrImportPreview:
        return self._repository.preview(selection)

    def confirm(self, selection: OcrSelection, *, command_id: str | None = None) -> OcrImportResult:
        return self._repository.confirm(selection, command_id or str(uuid4()))

    def check_integrity(self) -> None:
        self._repository.check_integrity()
