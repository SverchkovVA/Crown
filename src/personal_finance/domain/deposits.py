"""Чистый сценарный прогноз процентов; не создаёт фактические выплаты."""

from calendar import isleap
from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal
from enum import StrEnum

from personal_finance.domain.errors import ValidationError
from personal_finance.domain.ledger import require_identifier
from personal_finance.domain.models import MAX_MINOR, Money

RATE_SCALE = 10**8
INTEREST_DENOMINATOR = 100 * RATE_SCALE * 365 * 366
MAX_TERM_DAYS = 36600
MAX_EVENTS = 10000


class AccrualBoundary(StrEnum):
    OPENING_INCLUDED = "opening_included"
    MATURITY_INCLUDED = "maturity_included"


class BalanceMoment(StrEnum):
    BEFORE_MOVEMENTS = "before_movements"
    AFTER_MOVEMENTS = "after_movements"


class DayCount(StrEnum):
    FIXED_365 = "fixed_365"
    CALENDAR_YEAR = "calendar_year"


class InterestRounding(StrEnum):
    HALF_UP = "half_up"
    HALF_EVEN = "half_even"
    DOWN = "down"


class RoundingStage(StrEnum):
    DAILY = "daily"
    PAYOUT = "payout"


def _rate_units(value: Decimal) -> int:
    if not isinstance(value, Decimal) or not value.is_finite() or not 0 <= value <= 1000:
        raise ValidationError("Укажите конечную ставку Decimal от 0 до 1000 процентов.")
    parts = value.as_tuple()
    extra = -int(parts.exponent) - 8
    if len(parts.digits) > 32 or extra > 0 and any(parts.digits[-extra:]):
        raise ValidationError("В ставке допустимо не более 8 значащих дробных знаков.")
    numerator, denominator = value.as_integer_ratio()
    units, remainder = divmod(numerator * RATE_SCALE, denominator)
    if remainder:
        raise ValidationError("В ставке допустимо не более 8 знаков после запятой.")
    return units


def _rounded(numerator: int, policy: InterestRounding) -> int:
    whole, remainder = divmod(numerator, INTEREST_DENOMINATOR)
    twice = remainder * 2
    if policy == InterestRounding.HALF_UP:
        return whole + (twice >= INTEREST_DENOMINATOR)
    if policy == InterestRounding.HALF_EVEN:
        return whole + (
            twice > INTEREST_DENOMINATOR or twice == INTEREST_DENOMINATOR and whole % 2 == 1
        )
    return whole


@dataclass(frozen=True, slots=True)
class DepositRate:
    revision_id: str
    effective_on: date
    annual_percent: Decimal

    def __post_init__(self) -> None:
        require_identifier(self.revision_id)
        if type(self.effective_on) is not date:
            raise ValidationError("Укажите календарную дату версии ставки.")
        _rate_units(self.annual_percent)


@dataclass(frozen=True, slots=True)
class DepositMovement:
    id: str
    occurred_on: date
    amount: Money

    def __post_init__(self) -> None:
        require_identifier(self.id)
        if type(self.occurred_on) is not date:
            raise ValidationError("Укажите календарную дату движения вклада.")
        if not isinstance(self.amount, Money) or not self.amount.minor:
            raise ValidationError("Движение вклада должно иметь ненулевую сумму Money.")


@dataclass(frozen=True, slots=True, kw_only=True)
class DepositScenario:
    opened_on: date
    matures_on: date
    initial: Money
    rates: tuple[DepositRate, ...]
    movements: tuple[DepositMovement, ...]
    payout_dates: tuple[date, ...]
    boundary: AccrualBoundary
    balance_moment: BalanceMoment
    day_count: DayCount
    rounding: InterestRounding
    rounding_stage: RoundingStage
    capitalize: bool
    allow_topups: bool
    allow_withdrawals: bool
    minimum: Money

    def __post_init__(self) -> None:
        if type(self.opened_on) is not date or type(self.matures_on) is not date:
            raise ValidationError("Укажите даты открытия и окончания вклада.")
        if not 1 <= (self.matures_on - self.opened_on).days <= MAX_TERM_DAYS:
            raise ValidationError("Срок сценария должен быть от 1 до 36600 дней.")
        if (
            not isinstance(self.initial, Money)
            or self.initial.minor <= 0
            or not isinstance(self.minimum, Money)
            or self.minimum.currency != self.initial.currency
            or not 0 <= self.minimum.minor <= self.initial.minor
        ):
            raise ValidationError("Проверьте начальную сумму и неснижаемый остаток.")
        for value, kind in (
            (self.boundary, AccrualBoundary),
            (self.balance_moment, BalanceMoment),
            (self.day_count, DayCount),
            (self.rounding, InterestRounding),
            (self.rounding_stage, RoundingStage),
        ):
            if not isinstance(value, kind):
                raise ValidationError("Укажите явные правила расчёта вклада.")
        if any(
            type(v) is not bool
            for v in (self.capitalize, self.allow_topups, self.allow_withdrawals)
        ):
            raise ValidationError("Укажите разрешения пополнения, снятия и капитализации.")
        if (
            not isinstance(self.rates, tuple)
            or not 1 <= len(self.rates) <= MAX_EVENTS
            or any(not isinstance(v, DepositRate) for v in self.rates)
        ):
            raise ValidationError("Укажите от 1 до 10000 версий ставки.")
        rates = tuple(sorted(self.rates, key=lambda v: v.effective_on))
        if (
            rates[0].effective_on != self.opened_on
            or rates[-1].effective_on > self.matures_on
            or len({v.effective_on for v in rates}) != len(rates)
            or len({v.revision_id for v in rates}) != len(rates)
        ):
            raise ValidationError("Версии ставки повторяются или не покрывают срок вклада.")
        if (
            not isinstance(self.movements, tuple)
            or len(self.movements) > MAX_EVENTS
            or any(not isinstance(v, DepositMovement) for v in self.movements)
        ):
            raise ValidationError("Укажите не более 10000 движений вклада.")
        if len({v.id for v in self.movements}) != len(self.movements):
            raise ValidationError("Идентификатор движения вклада повторяется.")
        for movement in self.movements:
            if (
                not self.opened_on <= movement.occurred_on <= self.matures_on
                or movement.amount.currency != self.initial.currency
            ):
                raise ValidationError("Дата или валюта движения не соответствует сценарию.")
            if movement.amount.minor > 0 and not self.allow_topups:
                raise ValidationError("Условия сценария запрещают пополнение.")
            if movement.amount.minor < 0 and not self.allow_withdrawals:
                raise ValidationError("Условия сценария запрещают снятие.")
        if (
            not isinstance(self.payout_dates, tuple)
            or not 1 <= len(self.payout_dates) <= MAX_EVENTS
            or any(type(v) is not date for v in self.payout_dates)
        ):
            raise ValidationError("Укажите календарные даты выплат.")
        payouts = tuple(sorted(self.payout_dates))
        if (
            not self.opened_on < payouts[0]
            or payouts[-1] != self.matures_on
            or len(set(payouts)) != len(payouts)
        ):
            raise ValidationError("Последняя выплата должна быть в дату окончания, без повторов.")
        object.__setattr__(self, "rates", rates)
        object.__setattr__(
            self, "movements", tuple(sorted(self.movements, key=lambda v: (v.occurred_on, v.id)))
        )
        object.__setattr__(self, "payout_dates", payouts)


@dataclass(frozen=True, slots=True)
class DepositDay:
    day: date
    rate_revision_id: str
    base_minor: int
    movement_minor: int
    interest_numerator: int
    payout_minor: int
    capitalized_minor: int
    closing_minor: int


@dataclass(frozen=True, slots=True)
class DepositForecast:
    scenario: DepositScenario
    days: tuple[DepositDay, ...]

    @property
    def interest_minor(self) -> int:
        return sum(v.payout_minor for v in self.days)

    @property
    def closing_minor(self) -> int:
        return self.days[-1].closing_minor


def forecast_deposit(scenario: DepositScenario) -> DepositForecast:
    if not isinstance(scenario, DepositScenario):
        raise ValidationError("Передайте проверенный сценарий вклада.")
    movements: dict[date, int] = {}
    for v in scenario.movements:
        movements[v.occurred_on] = movements.get(v.occurred_on, 0) + v.amount.minor
    payouts = set(scenario.payout_dates)
    rate_index = 0
    balance = scenario.initial.minor
    accrued = 0
    result = []
    for offset in range((scenario.matures_on - scenario.opened_on).days + 1):
        day = scenario.opened_on + timedelta(days=offset)
        while (
            rate_index + 1 < len(scenario.rates)
            and scenario.rates[rate_index + 1].effective_on <= day
        ):
            rate_index += 1
        rate = scenario.rates[rate_index]
        movement = movements.get(day, 0)
        before = balance
        balance += movement
        if not scenario.minimum.minor <= balance <= MAX_MINOR:
            raise ValidationError("Движение нарушает остаток или допустимый диапазон вклада.")
        base = before if scenario.balance_moment == BalanceMoment.BEFORE_MOVEMENTS else balance
        included = (
            day != scenario.matures_on
            if scenario.boundary == AccrualBoundary.OPENING_INCLUDED
            else day != scenario.opened_on
        )
        denominator = (
            366 if scenario.day_count == DayCount.CALENDAR_YEAR and isleap(day.year) else 365
        )
        numerator = (
            base * _rate_units(rate.annual_percent) * (365 * 366 // denominator) if included else 0
        )
        accrued += (
            _rounded(numerator, scenario.rounding) * INTEREST_DENOMINATOR
            if scenario.rounding_stage == RoundingStage.DAILY
            else numerator
        )
        payout = _rounded(accrued, scenario.rounding) if day in payouts else 0
        capitalized = payout if scenario.capitalize else 0
        if payout > MAX_MINOR or balance + capitalized > MAX_MINOR:
            raise ValidationError("Проценты выходят за допустимый диапазон Money.")
        balance += capitalized
        if day in payouts:
            accrued = 0
        result.append(
            DepositDay(
                day, rate.revision_id, base, movement, numerator, payout, capitalized, balance
            )
        )
    return DepositForecast(scenario, tuple(result))
