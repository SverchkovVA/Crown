import json
from dataclasses import replace
from decimal import Decimal, Inexact, localcontext

import pytest

import personal_finance.domain.deposit_scenarios as codec
from personal_finance.domain.deposits import forecast_deposit
from personal_finance.domain.errors import ValidationError
from personal_finance.domain.models import Currency, Money
from tests.unit.test_deposits import scenario


@pytest.mark.parametrize("exponent", [0, 2, 8])
def test_round_trip_preserves_conditions_and_exact_forecast(exponent):
    value = scenario()
    currency = Currency("RUB" if exponent == 2 else "XYZ", exponent)
    value = replace(
        value,
        initial=Money(value.initial.minor, currency),
        minimum=Money(0, currency),
        movements=tuple(
            replace(m, amount=Money(m.amount.minor, currency)) for m in value.movements
        ),
    )
    raw = codec.scenario_json(value)
    restored = codec.scenario_from_json(raw)
    assert restored == value
    assert forecast_deposit(restored) == forecast_deposit(value)
    assert codec.scenario_json(restored) == raw
    assert json.loads(raw)["algorithm"] == "deposit-daily-v1"
    assert isinstance(json.loads(raw)["initial_minor"], int)


@pytest.mark.parametrize(
    "rate", ["0", "-0.0000", "0E-999999999", "1E+3", "7.12345678", "12.500000000000000000000"]
)
def test_decimal_is_canonical_plain_and_context_independent(rate):
    value = scenario()
    value = replace(
        value, rates=(replace(value.rates[0], annual_percent=Decimal(rate)),) + value.rates[1:]
    )
    before = codec.scenario_json(value)
    with localcontext() as ctx:
        ctx.prec = 1
        ctx.traps[Inexact] = True
        restored = codec.scenario_from_json(before)
        assert codec.scenario_json(restored) == before
        assert forecast_deposit(restored) == forecast_deposit(value)
    assert "E-" not in json.loads(before)["rates"][0]["annual_percent"]


@pytest.mark.parametrize(
    "field,value",
    [
        ("format", True),
        ("format", 2),
        ("format", "1"),
        ("algorithm", "next-version"),
        ("algorithm", None),
        ("initial_minor", 100001.0),
        ("initial_minor", True),
        ("initial_minor", "100001"),
        ("minimum_minor", False),
        ("minimum_minor", -1),
        ("opened_on", "20231230"),
        ("opened_on", "2023-12-30T00:00:00"),
        ("matures_on", "2024-02-30"),
        ("boundary", "guess"),
        ("balance_moment", False),
        ("day_count", "bank"),
        ("rounding", "nearest"),
        ("rounding_stage", None),
        ("capitalize", 1),
        ("allow_topups", "true"),
        ("allow_withdrawals", None),
        ("rates", []),
        ("rates", None),
        ("rates", [{}] * 10001),
        ("movements", None),
        ("movements", [{}] * 10001),
        ("payout_dates", []),
        ("payout_dates", ["2024-01-01", "2024-01-01", "2024-01-04"]),
    ],
)
def test_reject_invalid_root_field_without_echo(field, value):
    raw = codec.scenario_payload(scenario())
    raw[field] = value
    with pytest.raises(ValidationError):
        codec.scenario_from_payload(raw)


@pytest.mark.parametrize(
    "group,key,value",
    [
        ("currency", "exponent", True),
        ("currency", "code", "rub"),
        ("rates", "annual_percent", 12.5),
        ("rates", "annual_percent", "NaN"),
        ("rates", "annual_percent", "Infinity"),
        ("rates", "annual_percent", "0.000000001"),
        ("rates", "annual_percent", "7.123456780"),
        ("rates", "annual_percent", "1E+2"),
        ("rates", "revision_id", "private-marker-invalid"),
        ("rates", "effective_on", "2024-01-04"),
        ("movements", "minor", True),
        ("movements", "minor", 1.0),
        ("movements", "id", "private-marker-invalid"),
        ("movements", "occurred_on", "2024-01-05"),
    ],
)
def test_reject_invalid_nested_field(group, key, value):
    raw = codec.scenario_payload(scenario())
    row = raw[group] if group == "currency" else raw[group][0]
    row[key] = value
    with pytest.raises(ValidationError) as error:
        codec.scenario_from_payload(raw)
    assert "private-marker" not in str(error.value)


@pytest.mark.parametrize(
    "change",
    [
        "extra",
        "missing",
        "rate_extra",
        "movement_missing",
        "currency_extra",
        "rate_order",
        "movement_order",
        "payout_order",
    ],
)
def test_reject_ambiguous_noncanonical_definition(change):
    raw = codec.scenario_payload(scenario())
    if change == "extra":
        raw["extra"] = None
    if change == "missing":
        raw.pop("capitalize")
    if change == "rate_extra":
        raw["rates"][0]["extra"] = False
    if change == "movement_missing":
        raw["movements"][0].pop("minor")
    if change == "currency_extra":
        raw["currency"]["extra"] = False
    if change == "rate_order":
        raw["rates"].reverse()
    if change == "movement_order":
        raw["movements"].reverse()
    if change == "payout_order":
        raw["payout_dates"].reverse()
    with pytest.raises(ValidationError):
        codec.scenario_from_payload(raw)


@pytest.mark.parametrize(
    "raw",
    [
        None,
        [],
        3,
        True,
        {},
        "",
        "[]",
        '{"secret-marker":NaN}',
        '{"secret-marker":Infinity}',
        '{"format":1,"format":1}',
        "[" * 2000 + "]" * 2000,
        '"\ud800"',
        "x" * (codec.MAX_SCENARIO_JSON + 1),
        "я" * (codec.MAX_SCENARIO_JSON // 2 + 1),
    ],
    ids=lambda value: type(value).__name__,
)
def test_bad_json_is_bounded_safe_and_does_not_echo(raw):
    with pytest.raises(ValidationError) as error:
        codec.scenario_from_json(raw)
    assert "secret-marker" not in str(error.value)


@pytest.mark.parametrize(
    "change", ["spaces", "order", "newline", "duplicate", "float_money", "float_rate"]
)
def test_raw_json_must_be_canonical(change):
    raw = codec.scenario_payload(scenario())
    text = codec.scenario_json(scenario())
    if change == "spaces":
        text = json.dumps(raw, sort_keys=True)
    if change == "order":
        text = json.dumps(raw, separators=(",", ":"))
    if change == "newline":
        text += "\n"
    if change == "duplicate":
        text = text.replace('"format":1', '"format":1,"format":1')
    if change == "float_money":
        text = text.replace('"initial_minor":100001', '"initial_minor":100001.0')
    if change == "float_rate":
        text = text.replace('"annual_percent":"7.12345678"', '"annual_percent":7.12345678')
    with pytest.raises(ValidationError):
        codec.scenario_from_json(text)


def test_returned_payload_does_not_mutate_frozen_scenario():
    value = scenario()
    before = codec.scenario_json(value)
    raw = codec.scenario_payload(value)
    raw["rates"][0]["annual_percent"] = "99"
    raw["movements"].clear()
    assert codec.scenario_json(value) == before


@pytest.mark.parametrize(
    "rates,movements", [(False, False), (True, False), (False, True), (True, True)]
)
def test_duplicate_nested_ids_rejected(rates, movements):
    raw = codec.scenario_payload(scenario())
    if not rates and not movements:
        raw["rates"][1]["effective_on"] = raw["rates"][0]["effective_on"]
    if rates:
        raw["rates"][1]["revision_id"] = raw["rates"][0]["revision_id"]
    if movements:
        raw["movements"][1]["id"] = raw["movements"][0]["id"]
    with pytest.raises(ValidationError):
        codec.scenario_from_payload(raw)
