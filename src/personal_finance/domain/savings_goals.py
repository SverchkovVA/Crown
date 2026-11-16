"""План накоплений с равными ежемесячными пополнениями, без процентов."""

from collections.abc import Sequence
from dataclasses import dataclass, replace
from datetime import date
from itertools import groupby

from personal_finance.domain.analytics import ReportPeriod, ReportScope
from personal_finance.domain.analytics_cashflow import CashMovement, cash_flow_report
from personal_finance.domain.calendar_dates import add_calendar_months
from personal_finance.domain.errors import ValidationError
from personal_finance.domain.ledger import Operation
from personal_finance.domain.models import Account, Money


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


def _progress_basis_points(balance: int, target: int) -> int:
    return min(max(balance, 0), target) * 10000 // target


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
        _progress_basis_points(scenario.current.minor, scenario.target.minor),
        add_calendar_months(scenario.as_of, 1),
        count,
        reached_on,
        payments,
        required,
        projected,
    )


@dataclass(frozen=True, slots=True)
class SavingsGoalAccountProgress:
    account: Account
    projection: SavingsGoalProjection
    sources: tuple[CashMovement, ...]


def savings_goal_account_progress(
    scenario: SavingsGoalScenario, account: Account, operations: Sequence[Operation]
) -> SavingsGoalAccountProgress:
    currency = scenario.target.currency
    if account.currency != currency.code:
        raise ValidationError("Выберите счёт в валюте цели.")
    report = cash_flow_report(
        operations,
        ReportPeriod(scenario.as_of, scenario.as_of),
        ReportScope(frozenset((account.id,))),
    )
    position = report.accounts[0].totals if report.accounts else None
    if position is not None and position.currency != currency:
        raise ValidationError("Валюта движений счёта отличается от валюты цели.")
    balance = position.closing_minor if position is not None else 0
    if balance < 0:
        raise ValidationError(
            "На дату расчёта у счёта отрицательный остаток. Выберите другой счёт."
        )
    based_on_account = replace(scenario, current=Money(balance, currency))
    return SavingsGoalAccountProgress(
        account, project_savings_goal(based_on_account), report.movements
    )


@dataclass(frozen=True, slots=True)
class SavingsGoalDay:
    day: date
    balance_minor: int
    progress_basis_points: int
    sources: tuple[CashMovement, ...]


def savings_goal_history(result: SavingsGoalAccountProgress) -> tuple[SavingsGoalDay, ...]:
    """Закрывающие остатки по датам одного снимка, от первой проводки до среза."""
    scenario = result.projection.scenario
    rows = []
    balance = 0
    for day, movements in groupby(
        result.sources, key=lambda source: source.operation.draft.occurred_on
    ):
        sources = tuple(movements)
        balance += sum(source.minor for source in sources)
        rows.append(
            SavingsGoalDay(
                day, balance, _progress_basis_points(balance, scenario.target.minor), sources
            )
        )
    if not rows or rows[-1].day != scenario.as_of:
        rows.append(
            SavingsGoalDay(
                scenario.as_of, balance, _progress_basis_points(balance, scenario.target.minor), ()
            )
        )
    return tuple(rows)
