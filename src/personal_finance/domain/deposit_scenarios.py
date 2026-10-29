"""Строгий контракт условий сценария вклада; без SQL и финансовой записи."""

import json
from dataclasses import dataclass
from datetime import date
from decimal import Decimal, InvalidOperation
from typing import Any

from personal_finance.domain.deposits import (
    AccrualBoundary,
    BalanceMoment,
    DayCount,
    DepositMovement,
    DepositRate,
    DepositScenario,
    InterestRounding,
    RoundingStage,
)
from personal_finance.domain.errors import ValidationError
from personal_finance.domain.models import Currency, Money

MAX_SCENARIO_JSON = 4 * 1024 * 1024
_FIELDS = frozenset(
    {
        "format",
        "algorithm",
        "currency",
        "opened_on",
        "matures_on",
        "initial_minor",
        "minimum_minor",
        "rates",
        "movements",
        "payout_dates",
        "boundary",
        "balance_moment",
        "day_count",
        "rounding",
        "rounding_stage",
        "capitalize",
        "allow_topups",
        "allow_withdrawals",
    }
)


def _decimal_text(value: Decimal) -> str:
    if not value:
        return "0"
    text = format(value, "f")
    return text.rstrip("0").rstrip(".") if "." in text else text


def scenario_payload(value: DepositScenario) -> dict[str, Any]:
    if not isinstance(value, DepositScenario):
        raise ValidationError("Передайте проверенный сценарий вклада.")
    return {
        "format": 1,
        "algorithm": "deposit-daily-v1",
        "currency": {
            "code": value.initial.currency.code,
            "exponent": value.initial.currency.exponent,
        },
        "opened_on": value.opened_on.isoformat(),
        "matures_on": value.matures_on.isoformat(),
        "initial_minor": value.initial.minor,
        "minimum_minor": value.minimum.minor,
        "rates": [
            {
                "revision_id": v.revision_id,
                "effective_on": v.effective_on.isoformat(),
                "annual_percent": _decimal_text(v.annual_percent),
            }
            for v in value.rates
        ],
        "movements": [
            {"id": v.id, "occurred_on": v.occurred_on.isoformat(), "minor": v.amount.minor}
            for v in value.movements
        ],
        "payout_dates": [v.isoformat() for v in value.payout_dates],
        "boundary": value.boundary.value,
        "balance_moment": value.balance_moment.value,
        "day_count": value.day_count.value,
        "rounding": value.rounding.value,
        "rounding_stage": value.rounding_stage.value,
        "capitalize": value.capitalize,
        "allow_topups": value.allow_topups,
        "allow_withdrawals": value.allow_withdrawals,
    }


def _object(value: object, fields: frozenset[str]) -> dict[str, Any]:
    if type(value) is not dict or len(value) != len(fields) or set(value) != fields:
        raise ValidationError("Неверный состав полей сценария вклада.")
    return value


def _text(value: object, maximum: int = 64) -> str:
    if type(value) is not str or not 1 <= len(value) <= maximum:
        raise ValidationError("Некорректное текстовое поле сценария.")
    return value


def _integer(value: object) -> int:
    if type(value) is not int:
        raise ValidationError("Деньги и точность валюты должны быть целыми числами.")
    return value


def _flag(value: object) -> bool:
    if type(value) is not bool:
        raise ValidationError("Некорректный флаг условий сценария.")
    return value


def _day(value: object) -> date:
    text = _text(value, 10)
    result = date.fromisoformat(text)
    if result.isoformat() != text:
        raise ValidationError("Дата сценария должна быть записана как ГГГГ-ММ-ДД.")
    return result


def _array(value: object, *, empty: bool = False) -> list[Any]:
    if type(value) is not list or not (0 if empty else 1) <= len(value) <= 10000:
        raise ValidationError("В группе сценария допустимо до 10000 строк.")
    return value


def scenario_from_payload(raw: object) -> DepositScenario:
    value = _object(raw, _FIELDS)
    if type(value["format"]) is not int or value["format"] != 1:
        raise ValidationError("Неизвестная версия формата сценария вклада.")
    if type(value["algorithm"]) is not str or value["algorithm"] != "deposit-daily-v1":
        raise ValidationError("Неизвестная версия расчёта сценария вклада.")
    try:
        c = _object(value["currency"], frozenset({"code", "exponent"}))
        currency = Currency(_text(c["code"], 3), _integer(c["exponent"]))
        rates = []
        for item in _array(value["rates"]):
            row = _object(item, frozenset({"revision_id", "effective_on", "annual_percent"}))
            rates.append(
                DepositRate(
                    _text(row["revision_id"], 36),
                    _day(row["effective_on"]),
                    Decimal(_text(row["annual_percent"])),
                )
            )
        movements = []
        for item in _array(value["movements"], empty=True):
            row = _object(item, frozenset({"id", "occurred_on", "minor"}))
            movements.append(
                DepositMovement(
                    _text(row["id"], 36),
                    _day(row["occurred_on"]),
                    Money(_integer(row["minor"]), currency),
                )
            )
        result = DepositScenario(
            opened_on=_day(value["opened_on"]),
            matures_on=_day(value["matures_on"]),
            initial=Money(_integer(value["initial_minor"]), currency),
            minimum=Money(_integer(value["minimum_minor"]), currency),
            rates=tuple(rates),
            movements=tuple(movements),
            payout_dates=tuple(_day(v) for v in _array(value["payout_dates"])),
            boundary=AccrualBoundary(_text(value["boundary"])),
            balance_moment=BalanceMoment(_text(value["balance_moment"])),
            day_count=DayCount(_text(value["day_count"])),
            rounding=InterestRounding(_text(value["rounding"])),
            rounding_stage=RoundingStage(_text(value["rounding_stage"])),
            capitalize=_flag(value["capitalize"]),
            allow_topups=_flag(value["allow_topups"]),
            allow_withdrawals=_flag(value["allow_withdrawals"]),
        )
    except (ValueError, InvalidOperation):
        raise ValidationError("Сценарий не соответствует формату 1.") from None
    if scenario_payload(result) != value:
        raise ValidationError("Поля сценария записаны не в каноническом виде.")
    return result


def scenario_json(value: DepositScenario) -> str:
    result = json.dumps(
        scenario_payload(value),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )
    if len(result.encode("utf-8")) > MAX_SCENARIO_JSON:
        raise ValidationError("Сценарий превышает допустимые 4 MiB.")
    return result


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result = {}
    for name, value in pairs:
        if name in result:
            raise ValidationError("Поле сценария повторяется.")
        result[name] = value
    return result


def _invalid_constant(value: str) -> None:
    raise ValidationError("Нечисловые константы JSON запрещены.")


def scenario_from_json(text: object) -> DepositScenario:
    if type(text) is not str or len(text) > MAX_SCENARIO_JSON:
        raise ValidationError("Некорректный размер JSON сценария.")
    try:
        encoded = text.encode("utf-8")
    except UnicodeError:
        raise ValidationError("JSON сценария должен содержать корректный UTF-8.") from None
    if len(encoded) > MAX_SCENARIO_JSON:
        raise ValidationError("Некорректный размер JSON сценария.")
    try:
        raw = json.loads(text, object_pairs_hook=_unique_object, parse_constant=_invalid_constant)
    except (ValueError, RecursionError):
        raise ValidationError("Не удалось прочитать сценарий вклада.") from None
    result = scenario_from_payload(raw)
    if scenario_json(result) != text:
        raise ValidationError("JSON сценария записан не в каноническом виде.")
    return result


@dataclass(frozen=True, slots=True)
class SavedDepositScenario:
    id: str
    revision_id: str
    name: str
    scenario: DepositScenario
    is_deleted: bool
    created_at: str
