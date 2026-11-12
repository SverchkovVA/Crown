import json
from datetime import date

import pytest

from personal_finance.domain.errors import ValidationError
from personal_finance.domain.models import Currency, Money
from personal_finance.domain.planning_scenarios import (
    PlanningKind,
    planning_from_json,
    planning_json,
    planning_kind,
    planning_payload,
)
from personal_finance.domain.recurring_payments import (
    PaymentCalendarScenario,
    PaymentDirection,
    PaymentSchedule,
    RepeatUnit,
)


def calendar():
    return PaymentCalendarScenario(
        (
            PaymentSchedule(
                "Подписка",
                Money(500),
                PaymentDirection.EXPENSE,
                date(2026, 1, 31),
                RepeatUnit.MONTHS,
                2,
            ),
            PaymentSchedule(
                "Проценты",
                Money(800, Currency("JPY", 0)),
                PaymentDirection.INCOME,
                date(2026, 3, 31),
            ),
        )
    )


def test_calendar_codec_roundtrips_exact_rule_order_and_currency():
    value = calendar()
    assert planning_kind(value) is PlanningKind.PAYMENT_CALENDAR
    assert planning_from_json(planning_json(value)) == value
    assert planning_payload(value)["algorithm"] == "calendar-anchors-v1"
    assert planning_payload(value)["schedules"][1]["currency"] == {"code": "JPY", "exponent": 0}


@pytest.mark.parametrize(
    "field,bad",
    (
        ("amount_minor", True),
        ("amount_minor", 0),
        ("interval", 1201),
        ("interval", 2.0),
        ("last_on", "2026-01-30"),
        ("first_on", "2026-02-31"),
        ("direction", "transfer"),
        ("unit", "once"),
        ("name", " Подписка "),
        ("currency", {"code": "RUB", "exponent": True}),
    ),
)
def test_calendar_codec_rejects_noncanonical_or_invalid_schedule(field, bad):
    value = planning_payload(calendar())
    value["schedules"][0][field] = bad
    with pytest.raises(ValidationError):
        planning_from_json(
            json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        )


def test_calendar_codec_rejects_extra_fields_and_conflicting_currency_precision():
    value = planning_payload(calendar())
    value["schedules"][1]["currency"] = {"code": "RUB", "exponent": 0}
    with pytest.raises(ValidationError, match="точностью"):
        planning_from_json(
            json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        )
    value["schedules"][1]["currency"] = {"code": "JPY", "exponent": 0}
    value["schedules"][1]["unexpected"] = ""
    with pytest.raises(ValidationError, match="состав"):
        planning_from_json(
            json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        )
