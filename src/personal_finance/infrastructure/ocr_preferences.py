"""Локальные несекретные параметры OCR; имена отправителей здесь не хранятся."""

from PySide6.QtCore import QSettings

from personal_finance.domain.errors import ValidationError
from personal_finance.domain.ocr_settings import OcrPreferences


class LocalOcrPreferences:
    def __init__(self, settings: QSettings | None = None) -> None:
        self.settings = settings if settings is not None else QSettings("Crown", "OCR")

    def load(self) -> OcrPreferences:
        year = self.settings.value("reference_year", "")
        try:
            reference = int(str(year)) if year not in (None, "") else None
            return OcrPreferences(
                default_currency=str(self.settings.value("default_currency", "RUB")),
                reference_year=reference,
                confirm_fields=str(self.settings.value("confirm_fields", "true")).lower() == "true",
                review_before_save=str(self.settings.value("review_before_save", "true")).lower()
                == "true",
            )
        except (ValidationError, ValueError, TypeError):
            return OcrPreferences()

    def save(self, value: OcrPreferences) -> None:
        self.settings.setValue("default_currency", value.default_currency)
        self.settings.setValue(
            "reference_year", value.reference_year if value.reference_year else ""
        )
        self.settings.setValue("confirm_fields", "true" if value.confirm_fields else "false")
        self.settings.setValue(
            "review_before_save", "true" if value.review_before_save else "false"
        )
        self.settings.sync()
