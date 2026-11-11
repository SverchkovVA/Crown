"""План накоплений с равными ежемесячными пополнениями, без процентов."""

from dataclasses import dataclass
from datetime import date

from personal_finance.domain.calendar_dates import add_calendar_months
from personal_finance.domain.errors import ValidationError
from personal_finance.domain.models import Money


@dataclass(frozen=True, slots=True)
class SavingsGoalScenario:
    as_of: date
    current: Money
    target: Money
    monthly: Money
    deadline: date | None = None

    def __post_init__(self) -> None:
        if type(self.as_of) is not date or (
            self.deadline is not None
            and (type(self.deadline) is not date or self.deadline < self.as_of)
        ):
            raise ValidationError("Укажите дату расчёта и срок не раньше неё.")
        if not all(isinstance(value, Money) for value in (self.current, self.target, self.monthly)):
            raise ValidationError("Укажите точные денежные суммы цели.")
        if (
            self.current.currency != self.target.currency
            or self.monthly.currency != self.target.currency
        ):
            raise ValidationError("Все суммы цели должны быть в одной валюте.")
        if self.current.minor < 0 or self.monthly.minor < 0 or self.target.minor <= 0:
            raise ValidationError(
                "Цель должна быть больше нуля, накопления и пополнение — неотрицательными."
            )


@dataclass(frozen=True, slots=True)
class SavingsGoalProjection:
    scenario: SavingsGoalScenario
    remaining_minor: int
    progress_basis_points: int
    first_contribution_on: date | None
    contributions_to_goal: int | None
    reached_on: date | None
    contributions_by_deadline: int | None
    required_monthly_minor: int | None
    at_deadline_minor: int | None


def project_savings_goal(scenario: SavingsGoalScenario) -> SavingsGoalProjection:
    if not isinstance(scenario, SavingsGoalScenario):
        raise ValidationError("Укажите условия цели накоплений.")
    remaining = max(0, scenario.target.minor - scenario.current.minor)
    monthly = scenario.monthly.minor
    count = None
    if remaining == 0:
        count = 0
    elif monthly:
        count = (remaining + monthly - 1) // monthly
    reached_on = add_calendar_months(scenario.as_of, count) if count is not None else None
    payments = None
    required = None
    projected = None
    if scenario.deadline is not None:
        deadline = scenario.deadline
        payments = (
            (deadline.year - scenario.as_of.year) * 12 + deadline.month - scenario.as_of.month
        )
        last = add_calendar_months(scenario.as_of, payments)
        if payments and last is not None and last > deadline:
            payments -= 1
        if remaining == 0:
            required = 0
        elif payments:
            required = (remaining + payments - 1) // payments
        projected = scenario.current.minor + payments * monthly
    return SavingsGoalProjection(
        scenario,
        remaining,
        min(scenario.current.minor, scenario.target.minor) * 10000 // scenario.target.minor,
        add_calendar_months(scenario.as_of, 1),
        count,
        reached_on,
        payments,
        required,
        projected,
    )
