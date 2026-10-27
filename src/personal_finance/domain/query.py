"""Переносимый фильтр журнала: OR внутри группы, AND между группами."""

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date
from typing import Any

from personal_finance.domain.errors import ValidationError
from personal_finance.domain.ledger import Operation, OperationKind, require_identifier


def _ids(values: tuple[str, ...]) -> tuple[str, ...]:
    if not isinstance(values, tuple) or len(values) > 100:
        raise ValidationError("В одной группе фильтра допускается не более 100 значений.")
    for value in values:
        require_identifier(value)
    if len(set(values)) != len(values):
        raise ValidationError("Значение фильтра повторяется.")
    return tuple(sorted(values))


@dataclass(frozen=True, slots=True)
class OperationQuery:
    start: date | None = None
    end: date | None = None
    account_ids: tuple[str, ...] = ()
    category_ids: tuple[str, ...] = ()
    institution_ids: tuple[str, ...] = ()
    tag_ids: tuple[str, ...] = ()
    kinds: tuple[OperationKind, ...] = ()
    text: str = ""
    include_deleted: bool = False

    def __post_init__(self) -> None:
        if any(value is not None and type(value) is not date for value in (self.start, self.end)):
            raise ValidationError("Укажите календарные даты фильтра.")
        if self.start is not None and self.end is not None and self.start > self.end:
            raise ValidationError("Начало периода позже окончания.")
        for name in ("account_ids", "category_ids", "institution_ids", "tag_ids"):
            object.__setattr__(self, name, _ids(getattr(self, name)))
        if (
            not isinstance(self.kinds, tuple)
            or any(not isinstance(k, OperationKind) for k in self.kinds)
            or len(set(self.kinds)) != len(self.kinds)
        ):
            raise ValidationError("Некорректные виды операций фильтра.")
        object.__setattr__(self, "kinds", tuple(sorted(self.kinds, key=lambda k: k.value)))
        if (
            not isinstance(self.text, str)
            or len(self.text) > 500
            or any(ord(c) < 32 and c != "\n" for c in self.text)
            or type(self.include_deleted) is not bool
        ):
            raise ValidationError("Некорректный текст или признак удаления фильтра.")
        object.__setattr__(self, "text", self.text.strip())

    def matches(self, operation: Operation, institutions: Mapping[str, str | None]) -> bool:
        value = operation.draft
        if operation.is_deleted and not self.include_deleted:
            return False
        if self.start is not None and value.occurred_on < self.start:
            return False
        if self.end is not None and value.occurred_on > self.end:
            return False
        if self.kinds and value.kind not in self.kinds:
            return False
        accounts = {value.account_id}
        if value.destination_account_id:
            accounts.add(value.destination_account_id)
        if (self.account_ids or self.institution_ids) and not any(
            (not self.account_ids or account in self.account_ids)
            and (not self.institution_ids or institutions.get(account) in self.institution_ids)
            for account in accounts
        ):
            return False
        categories = {part.category_id for part in value.category_allocations()}
        if value.fee_category_id:
            categories.add(value.fee_category_id)
        if self.category_ids and not categories.intersection(self.category_ids):
            return False
        if not set(self.tag_ids).issubset(value.tag_ids):
            return False
        return not self.text or self.text.casefold() in value.note.casefold()

    def as_dict(self) -> dict[str, Any]:
        return {
            "version": 1,
            "start": self.start.isoformat() if self.start else None,
            "end": self.end.isoformat() if self.end else None,
            "account_ids": list(self.account_ids),
            "category_ids": list(self.category_ids),
            "institution_ids": list(self.institution_ids),
            "tag_ids": list(self.tag_ids),
            "kinds": [kind.value for kind in self.kinds],
            "text": self.text,
            "include_deleted": self.include_deleted,
        }

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "OperationQuery":
        expected = {
            "version",
            "start",
            "end",
            "account_ids",
            "category_ids",
            "institution_ids",
            "tag_ids",
            "kinds",
            "text",
            "include_deleted",
        }
        if (
            type(payload) is not dict
            or set(payload) != expected
            or type(payload["version"]) is not int
            or payload["version"] != 1
        ):
            raise ValidationError("Версия или состав сохранённого фильтра не поддерживается.")

        def day(name: str) -> date | None:
            value = payload[name]
            if value is None:
                return None
            if not isinstance(value, str):
                raise ValidationError("Некорректная дата фильтра.")
            parsed = date.fromisoformat(value)
            if parsed.isoformat() != value:
                raise ValidationError("Некорректная дата фильтра.")
            return parsed

        try:
            for name in ("account_ids", "category_ids", "institution_ids", "tag_ids", "kinds"):
                if type(payload[name]) is not list:
                    raise ValueError
            return cls(
                day("start"),
                day("end"),
                tuple(payload["account_ids"]),
                tuple(payload["category_ids"]),
                tuple(payload["institution_ids"]),
                tuple(payload["tag_ids"]),
                tuple(OperationKind(value) for value in payload["kinds"]),
                payload["text"],
                payload["include_deleted"],
            )
        except (ValueError, TypeError, KeyError) as exc:
            raise ValidationError("Некорректное содержимое сохранённого фильтра.") from exc
