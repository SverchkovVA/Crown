"""Недавние книги на этом компьютере: название и путь, без данных доступа."""

import json
from dataclasses import dataclass
from pathlib import Path
from uuid import UUID

from PySide6.QtCore import QSettings

from personal_finance.domain.errors import ValidationError
from personal_finance.domain.models import validate_name

MAX_RECENT = 12
MAX_SETTINGS_BYTES = 2 * 1024 * 1024


def default_settings() -> QSettings:
    return QSettings("Crown", "Books")


def _identifier(value: str) -> str:
    if not isinstance(value, str):
        raise ValidationError("Некорректный идентификатор книги.")
    try:
        if str(UUID(value)) != value:
            raise ValueError
    except ValueError:
        raise ValidationError("Некорректный идентификатор книги.") from None
    return value


@dataclass(frozen=True, slots=True)
class RecentBook:
    book_id: str
    path: str
    name: str


class LocalRecentBooks:
    def __init__(self, settings: QSettings | None = None) -> None:
        self.settings = settings if settings is not None else default_settings()

    def enabled(self, book_id: str) -> bool:
        key = "do_not_remember/" + _identifier(book_id)
        return str(self.settings.value(key, "false")).lower() not in ("true", "1")

    def items(self) -> tuple[RecentBook, ...]:
        raw = self.settings.value("recent_books_v1", "")
        if not isinstance(raw, str):
            return ()
        try:
            if len(raw.encode("utf-8")) > MAX_SETTINGS_BYTES:
                return ()
            rows = json.loads(raw) if raw else []
            if not isinstance(rows, list) or len(rows) > MAX_RECENT:
                return ()
            result = []
            seen = set()
            for row in rows:
                if not isinstance(row, dict) or set(row) != {"book_id", "path", "name"}:
                    continue
                identifier = _identifier(row["book_id"])
                path, name = row["path"], row["name"]
                if (
                    not isinstance(path, str)
                    or not Path(path).is_absolute()
                    or len(path) > 32768
                    or "\x00" in path
                    or not isinstance(name, str)
                    or not 1 <= len(name) <= 120
                ):
                    continue
                name = validate_name(name)
                canonical = str(Path(path))
                if Path(canonical) in seen or not self.enabled(identifier):
                    continue
                result.append(RecentBook(identifier, canonical, name))
                seen.add(Path(canonical))
            return tuple(result)
        except (ValueError, TypeError, ValidationError, UnicodeError):
            return ()

    def _save(self, items: tuple[RecentBook, ...]) -> None:
        raw = json.dumps(
            [{"book_id": v.book_id, "path": v.path, "name": v.name} for v in items[:MAX_RECENT]],
            ensure_ascii=False,
            separators=(",", ":"),
        )
        if len(raw.encode("utf-8")) > MAX_SETTINGS_BYTES:
            raise ValidationError("Слишком большой список недавних книг.")
        self.settings.setValue("recent_books_v1", raw)
        self.settings.sync()
        if self.settings.status() != QSettings.Status.NoError:
            raise ValidationError("Не удалось сохранить настройки списка недавних книг.")

    def remember(self, root: Path, book_id: str, name: str) -> None:
        _identifier(book_id)
        if not self.enabled(book_id):
            return
        if not isinstance(name, str) or not 1 <= len(name) <= 120:
            raise ValidationError("Некорректное название книги.")
        path = str(root.absolute())
        item = RecentBook(book_id, path, name)
        self._save((item, *(v for v in self.items() if Path(v.path) != Path(path))))

    def forget(self, path: str) -> None:
        self._save(tuple(v for v in self.items() if Path(v.path) != Path(path)))

    def set_enabled(self, book_id: str, enabled: bool) -> None:
        _identifier(book_id)
        if type(enabled) is not bool:
            raise ValidationError("Некорректная настройка недавних книг.")
        rows = self.items()
        key = "do_not_remember/" + book_id
        if enabled:
            self.settings.remove(key)
        else:
            self.settings.setValue(key, True)
            rows = tuple(v for v in rows if v.book_id != book_id)
        self._save(rows)
