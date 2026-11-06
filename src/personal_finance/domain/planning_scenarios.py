"""Строгий формат сохраняемых условий бюджета и цели, без SQL и проводок."""

import json
from dataclasses import dataclass, field
from datetime import date
from enum import StrEnum
from typing import Any

from personal_finance.domain.analytics import ReportPeriod, ReportScope
from personal_finance.domain.errors import ValidationError
from personal_finance.domain.models import Currency, Money
from personal_finance.domain.periods import AccountingMonth
from personal_finance.domain.savings_goals import SavingsGoalScenario

MAX_PLANNING_JSON = 4 * 1024 * 1024
MAX_SCOPE_IDENTIFIERS = 10000


class PlanningKind(StrEnum):
    BUDGET = "budget"
    SAVINGS_GOAL = "savings_goal"


class BudgetPeriodMode(StrEnum):
    MONTH = "month"
    DATES = "dates"


@dataclass(frozen=True, slots=True)
class BudgetScenario:
    period: ReportPeriod
    limit: Money
    scope: ReportScope = field(default_factory=ReportScope)
    period_mode: BudgetPeriodMode = BudgetPeriodMode.DATES

    def __post_init__(self) -> None:
        if (
            not isinstance(self.period, ReportPeriod)
            or not isinstance(self.limit, Money)
            or self.limit.minor < 0
            or not isinstance(self.scope, ReportScope)
            or not isinstance(self.period_mode, BudgetPeriodMode)
        ):
            raise ValidationError("Проверьте период, лимит и отбор бюджета.")
        if any(
            values is not None and len(values) > MAX_SCOPE_IDENTIFIERS
            for values in (self.scope.account_ids, self.scope.category_ids)
        ):
            raise ValidationError("В каждом отборе бюджета допустимо до 10000 записей.")
        if self.period_mode == BudgetPeriodMode.MONTH:
            month = AccountingMonth.from_date(self.period.start)
            if self.period.start != month.start or self.period.end != month.end:
                raise ValidationError(
                    "Месячный бюджет должен охватывать календарный месяц целиком."
                )


PlanningScenario = BudgetScenario | SavingsGoalScenario


def planning_kind(value: PlanningScenario) -> PlanningKind:
    if isinstance(value, BudgetScenario):
        return PlanningKind.BUDGET
    if isinstance(value, SavingsGoalScenario):
        return PlanningKind.SAVINGS_GOAL
    raise ValidationError("Передайте условия бюджета или цели накоплений.")


def planning_payload(value: PlanningScenario) -> dict[str, Any]:
    kind = planning_kind(value)
    currency = value.limit.currency if isinstance(value, BudgetScenario) else value.target.currency
    result: dict[str, Any] = {
        "format": 1,
        "kind": kind.value,
        "algorithm": (
            "budget-postings-v1" if kind == PlanningKind.BUDGET else "monthly-contribution-v1"
        ),
        "currency": {"code": currency.code, "exponent": currency.exponent},
    }
    if isinstance(value, BudgetScenario):
        result.update(
            period_mode=value.period_mode.value,
            start=value.period.start.isoformat(),
            end=value.period.end.isoformat(),
            limit_minor=value.limit.minor,
            account_ids=None
            if value.scope.account_ids is None
            else sorted(value.scope.account_ids),
            category_ids=None
            if value.scope.category_ids is None
            else sorted(value.scope.category_ids),
        )
    else:
        result.update(
            as_of=value.as_of.isoformat(),
            current_minor=value.current.minor,
            target_minor=value.target.minor,
            monthly_minor=value.monthly.minor,
            deadline=None if value.deadline is None else value.deadline.isoformat(),
        )
    return result


_COMMON = frozenset({"format", "kind", "algorithm", "currency"})
_BUDGET_FIELDS = _COMMON | {
    "period_mode",
    "start",
    "end",
    "limit_minor",
    "account_ids",
    "category_ids",
}
_GOAL_FIELDS = _COMMON | {"as_of", "current_minor", "target_minor", "monthly_minor", "deadline"}


def _object(value: object, fields: frozenset[str]) -> dict[str, Any]:
    if (
        type(value) is not dict
        or any(type(key) is not str for key in value)
        or set(value) != fields
    ):
        raise ValidationError("Неверный состав полей условий планирования.")
    return value


def _text(value: object, maximum: int = 64) -> str:
    if type(value) is not str or not 1 <= len(value) <= maximum:
        raise ValidationError("Некорректное текстовое поле условий планирования.")
    return value


def _integer(value: object) -> int:
    if type(value) is not int:
        raise ValidationError("Суммы и точность валюты должны быть целыми числами.")
    return value


def _day(value: object) -> date:
    text = _text(value, 10)
    result = date.fromisoformat(text)
    if result.isoformat() != text:
        raise ValidationError("Даты условий должны быть записаны как ГГГГ-ММ-ДД.")
    return result


def _identifiers(value: object) -> frozenset[str] | None:
    if value is None:
        return None
    if type(value) is not list or len(value) > MAX_SCOPE_IDENTIFIERS:
        raise ValidationError("В отборе бюджета допустимо до 10000 идентификаторов.")
    values = [_text(identifier, 36) for identifier in value]
    if len(set(values)) != len(values):
        raise ValidationError("Идентификатор в отборе бюджета повторяется.")
    return frozenset(values)


def planning_from_payload(raw: object) -> PlanningScenario:
    if type(raw) is not dict:
        raise ValidationError("Неверные условия планирования.")
    try:
        kind = PlanningKind(_text(raw.get("kind"), 32))
        value = _object(raw, _BUDGET_FIELDS if kind == PlanningKind.BUDGET else _GOAL_FIELDS)
        if type(value["format"]) is not int or value["format"] != 1:
            raise ValidationError("Неизвестная версия формата условий планирования.")
        algorithm = _text(value["algorithm"])
        expected = (
            "budget-postings-v1" if kind == PlanningKind.BUDGET else "monthly-contribution-v1"
        )
        if algorithm != expected:
            raise ValidationError("Неизвестная версия расчёта условий планирования.")
        c = _object(value["currency"], frozenset({"code", "exponent"}))
        currency = Currency(_text(c["code"], 3), _integer(c["exponent"]))
        result: PlanningScenario
        if kind == PlanningKind.BUDGET:
            result = BudgetScenario(
                ReportPeriod(_day(value["start"]), _day(value["end"])),
                Money(_integer(value["limit_minor"]), currency),
                ReportScope(
                    _identifiers(value["account_ids"]), _identifiers(value["category_ids"])
                ),
                BudgetPeriodMode(_text(value["period_mode"])),
            )
        else:
            result = SavingsGoalScenario(
                _day(value["as_of"]),
                Money(_integer(value["current_minor"]), currency),
                Money(_integer(value["target_minor"]), currency),
                Money(_integer(value["monthly_minor"]), currency),
                None if value["deadline"] is None else _day(value["deadline"]),
            )
    except (ValueError, TypeError):
        raise ValidationError("Условия не соответствуют формату планирования 1.") from None
    if planning_payload(result) != value:
        raise ValidationError("Поля условий планирования записаны не в каноническом виде.")
    return result


def planning_json(value: PlanningScenario) -> str:
    result = json.dumps(
        planning_payload(value),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )
    if len(result.encode("utf-8")) > MAX_PLANNING_JSON:
        raise ValidationError("Условия планирования превышают допустимые 4 MiB.")
    return result


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValidationError("Поле условий планирования повторяется.")
        result[key] = value
    return result


def _invalid_number(value: str) -> None:
    raise ValidationError("Дробные и нечисловые константы JSON запрещены.")


def planning_from_json(text: object) -> PlanningScenario:
    if type(text) is not str or len(text) > MAX_PLANNING_JSON:
        raise ValidationError("Некорректный размер JSON условий планирования.")
    try:
        encoded = text.encode("utf-8")
    except UnicodeError:
        raise ValidationError("JSON условий должен содержать корректный UTF-8.") from None
    if len(encoded) > MAX_PLANNING_JSON:
        raise ValidationError("Некорректный размер JSON условий планирования.")
    try:
        raw = json.loads(
            text,
            object_pairs_hook=_unique_object,
            parse_constant=_invalid_number,
            parse_float=_invalid_number,
        )
        result = planning_from_payload(raw)
    except (ValueError, RecursionError, OverflowError):
        raise ValidationError("Не удалось прочитать условия планирования.") from None
    if planning_json(result) != text:
        raise ValidationError("JSON условий планирования записан не в каноническом виде.")
    return result


@dataclass(frozen=True, slots=True)
class SavedPlanningScenario:
    id: str
    revision_id: str
    name: str
    scenario: PlanningScenario
    is_deleted: bool
    created_at: str

    @property
    def kind(self) -> PlanningKind:
        return planning_kind(self.scenario)
