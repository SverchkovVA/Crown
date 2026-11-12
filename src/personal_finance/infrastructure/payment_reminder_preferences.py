"""Локальный выбор календаря для напоминаний в конкретной реплике книги."""

from hashlib import sha256

from PySide6.QtCore import QSettings

from personal_finance.domain.errors import ValidationError
from personal_finance.domain.ledger import require_identifier


class PaymentReminderPreferences:
    def __init__(self, settings: QSettings | None = None) -> None:
        self.settings = settings if settings is not None else QSettings("Crown", "PaymentReminders")

    @staticmethod
    def _key(replica_id: str) -> str:
        require_identifier(replica_id)
        digest = sha256(replica_id.encode("ascii")).hexdigest()
        return f"calendars-v1/{digest}"

    def selection(self, replica_id: str) -> str | None:
        value = self.settings.value(self._key(replica_id))
        if not isinstance(value, str):
            return None
        try:
            require_identifier(value)
        except ValidationError:
            return None
        return value

    def select(self, replica_id: str, calendar_id: str | None) -> None:
        key = self._key(replica_id)
        if calendar_id is None:
            self.settings.remove(key)
        else:
            require_identifier(calendar_id)
            self.settings.setValue(key, calendar_id)
        self.settings.sync()
        if self.settings.status() is not QSettings.Status.NoError:
            raise ValidationError("Не удалось сохранить выбор календаря на этом ПК.")
