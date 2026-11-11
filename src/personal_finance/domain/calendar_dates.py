"""Календарный сдвиг с сохранением исходного дня месяца."""

from calendar import monthrange
from datetime import date


def add_calendar_months(anchor: date, offset: int) -> date | None:
    month_index = (anchor.year - 1) * 12 + anchor.month - 1 + offset
    if not 0 <= month_index < 9999 * 12:
        return None
    year_index, month_index = divmod(month_index, 12)
    year, month = year_index + 1, month_index + 1
    return date(year, month, min(anchor.day, monthrange(year, month)[1]))
