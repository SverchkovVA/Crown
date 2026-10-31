"""Общие календарные форматы; год по настройке остаётся отдельным предложением."""

import re
from dataclasses import dataclass
from datetime import date, time

from personal_finance.infrastructure.ocr.rub_fields import _MONTHS

MONTHS = {
    **_MONTHS,
    **dict(
        zip(
            ("янв", "фев", "мар", "апр", "май", "июн", "июл", "авг", "сен", "окт", "ноя", "дек"),
            range(1, 13),
            strict=True,
        )
    ),
}
MONTHS["сент"] = 9
_NAMES = "|".join(sorted(MONTHS, key=len, reverse=True))
_BODY = (
    r"(?:(?P<iso>[0-9]{4}-[0-9]{2}-[0-9]{2})|"
    r"(?P<day_num>[0-9]{1,2})[./](?P<month_num>[0-9]{1,2})(?:[./](?P<year_num>[0-9]{4}))?|"
    rf"(?P<day_text>[0-9]{{1,2}})\s*(?P<month_name>{_NAMES})\.?"
    r"(?:\s*(?P<year_text>[0-9]{4})(?:\s*г\.?)?)?)"
)
_CLOCK = r"(?:\s*(?:,|в)?\s*(?P<hour>[0-9]{1,2}):(?P<minute>[0-9]{2})(?::(?P<second>[0-9]{2}))?)?"
_FULL = re.compile(_BODY + _CLOCK, re.I)
_SCAN = re.compile(r"(?<![\w./-])" + _BODY + _CLOCK + r"(?![\w./:-])(?!\s+[0-9])", re.I)
_DAY_PREFIX = re.compile(
    r"^(?:сегодня|вчера|пн|вт|ср|чт|пт|сб|вс|понедельник|вторник|среда|четверг|"
    r"пятница|суббота|воскресенье),?\s+",
    re.I,
)
_LABEL = re.compile(
    r"^(?:дата(?:\s+(?:и время|операции|платежа|перевода|проведения))?|время)\s*:?\s*", re.I
)
_NAV = re.compile(r"^(?:[<‹›←]|[‘’']?>)\s*")


@dataclass(frozen=True)
class DateParts:
    day: int
    month: int
    year: int | None
    local_time: time | None
    invalid_time: bool = False

    def calendar(self, reference_year: int | None = None) -> date | None:
        year = self.year if self.year is not None else reference_year
        return date(year, self.month, self.day) if year is not None else None


def parse_date(text: str) -> DateParts | None:
    text = _DAY_PREFIX.sub("", _LABEL.sub("", _NAV.sub("", text.strip())))
    match = _FULL.fullmatch(text)
    if match is None:
        return None
    year: int | None
    if match["iso"]:
        year, month, day = map(int, match["iso"].split("-"))
    else:
        year_text = match["year_num"] or match["year_text"]
        year = int(year_text) if year_text else None
        month = (
            int(match["month_num"])
            if match["month_num"]
            else MONTHS[match["month_name"].casefold()]
        )
        day = int(match["day_num"] or match["day_text"])
    clock = None
    invalid = False
    if match["hour"]:
        try:
            clock = time(int(match["hour"]), int(match["minute"]), int(match["second"] or 0))
        except ValueError:
            invalid = True
    date(year if year is not None else 2000, month, day)
    return DateParts(day, month, year, clock, invalid)


def date_spans(text: str) -> list[tuple[int, int, str]]:
    # Неверный календарь тоже найден: поле получит INVALID, а не дату прежнего раздела.
    return [(m.start(), m.end(), m.group()) for m in _SCAN.finditer(text)]
