"""Параметры проверки OCR и явно заданные собственные отправители."""

import re
from dataclasses import dataclass, field
from datetime import date

from personal_finance.domain.errors import ValidationError
from personal_finance.domain.ledger import require_identifier
from personal_finance.domain.models import validate_name


def normalized_party(value: str) -> str:
    return " ".join(value.casefold().split())


@dataclass(frozen=True, slots=True)
class TransferParty:
    name: str = field(repr=False)
    account_id: str | None = None
    exclude: bool = False

    def __post_init__(self) -> None:
        if not isinstance(self.name, str) or validate_name(self.name) != self.name:
            raise ValidationError("Укажите имя отправителя без внешних пробелов.")
        if type(self.exclude) is not bool or self.exclude and self.account_id is not None:
            raise ValidationError("Для исключённого отправителя второй счёт не задаётся.")
        if self.account_id is not None:
            require_identifier(self.account_id)


@dataclass(frozen=True, slots=True)
class OcrPreferences:
    default_currency: str = "RUB"
    reference_year: int | None = None
    confirm_fields: bool = True
    review_before_save: bool = True
    transfer_parties: tuple[TransferParty, ...] = field(default=(), repr=False)

    def __post_init__(self) -> None:
        if (
            self.default_currency != "RUB"
            or self.reference_year is not None
            and (type(self.reference_year) is not int or not 1 <= self.reference_year <= 9999)
            or type(self.confirm_fields) is not bool
            or type(self.review_before_save) is not bool
            or not isinstance(self.transfer_parties, tuple)
            or len(self.transfer_parties) > 100
            or any(not isinstance(p, TransferParty) for p in self.transfer_parties)
        ):
            raise ValidationError("Некорректные параметры OCR.")
        names = [normalized_party(p.name) for p in self.transfer_parties]
        if len(set(names)) != len(names):
            raise ValidationError("Имена собственных отправителей не должны повторяться.")

    @property
    def year(self) -> int:
        return self.reference_year if self.reference_year is not None else date.today().year

    def matching_parties(self, description: str) -> tuple[TransferParty, ...]:
        text = normalized_party(description)
        return tuple(
            p
            for p in self.transfer_parties
            if re.search(r"(?<!\w)" + re.escape(normalized_party(p.name)) + r"(?!\w)", text)
        )
