from datetime import date, datetime

import pytest
from hypothesis import given
from hypothesis import strategies as st

from personal_finance.domain.errors import ValidationError
from personal_finance.domain.periods import AccountingMonth, validate_period_change


@given(st.dates())
def test_month_boundaries_and_canonical_roundtrip(value):
    month = AccountingMonth.from_date(value)
    assert month.start <= value <= month.end
    assert AccountingMonth.parse(month.key) == month
    assert month.start.day == 1 and month.end.month == value.month


@pytest.mark.parametrize(
    "value", ["2026-1", "2026-00", "2026-13", "0000-01", "2026-01-02", " 2026-01", None, 123]
)
def test_noncanonical_month_is_rejected(value):
    with pytest.raises(ValidationError):
        AccountingMonth.parse(value)


@pytest.mark.parametrize(
    "year,month", [(True, 1), (2026, True), (0, 1), (10000, 1), (2026, 0), (2026, 13), (2026.0, 1)]
)
def test_invalid_month_parts_are_rejected(year, month):
    with pytest.raises(ValidationError):
        AccountingMonth(year, month)


def test_leap_year_and_extreme_months():
    assert AccountingMonth(2024, 2).end == date(2024, 2, 29)
    assert AccountingMonth(2100, 2).end == date(2100, 2, 28)
    assert AccountingMonth(1, 1).start == date.min
    assert AccountingMonth(9999, 12).end == date.max
    with pytest.raises(ValidationError):
        AccountingMonth.from_date(datetime.now())


@pytest.mark.parametrize(
    "closed,reason", [(False, ""), (False, "  "), (1, "ok"), (True, "x" * 501), (True, "x\t")]
)
def test_invalid_transition_input_is_rejected(closed, reason):
    with pytest.raises(ValidationError):
        validate_period_change(AccountingMonth(2026, 1), closed, reason)


def test_reopen_requires_reason_and_only_reason_is_trimmed():
    month = AccountingMonth(2026, 1)
    assert validate_period_change(month, False, "  Исправление даты  ") == "Исправление даты"
    assert validate_period_change(month, True, "") == ""
