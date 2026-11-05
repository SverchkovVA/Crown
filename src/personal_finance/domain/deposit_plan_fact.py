"""Сравнение запланированных процентов с выбранным доходом журнала."""

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date

from personal_finance.domain.analytics import (
    OperationFlow,
    ReportPeriod,
    ReportScope,
    income_expense_report,
    report_sources,
)
from personal_finance.domain.deposits import DepositScenario, forecast_deposit
from personal_finance.domain.errors import ValidationError
from personal_finance.domain.ledger import Operation, OperationKind
from personal_finance.domain.models import Account, Category, CategoryKind, Currency


@dataclass(frozen=True, slots=True)
class DepositInterestMonth:
    month: date
    planned_minor: int
    actual_minor: int

    @property
    def difference_minor(self) -> int:
        return self.actual_minor - self.planned_minor


@dataclass(frozen=True, slots=True)
class DepositPlanFact:
    period: ReportPeriod
    currency: Currency
    months: tuple[DepositInterestMonth, ...]
    sources: tuple[OperationFlow, ...]

    @property
    def planned_minor(self) -> int:
        return sum(row.planned_minor for row in self.months)

    @property
    def actual_minor(self) -> int:
        return sum(row.actual_minor for row in self.months)

    @property
    def difference_minor(self) -> int:
        return self.actual_minor - self.planned_minor


def deposit_plan_fact(
    scenario: DepositScenario,
    operations: Sequence[Operation],
    account: Account,
    category: Category,
    through: date,
) -> DepositPlanFact:
    period = ReportPeriod(scenario.opened_on, through)
    currency = scenario.initial.currency
    if account.currency != currency.code:
        raise ValidationError("Выберите счёт в валюте сценария вклада.")
    if category.kind != CategoryKind.INCOME:
        raise ValidationError("Выберите доходную категорию процентов.")
    actual = income_expense_report(
        [value for value in operations if value.draft.kind == OperationKind.INCOME],
        period,
        ReportScope(frozenset((account.id,)), frozenset((category.id,))),
    )
    if any(value.currency != currency for value in actual.totals):
        raise ValidationError("Валюта записанных процентов отличается от валюты сценария.")

    payout_dates = set(scenario.payout_dates)
    planned: dict[date, int] = {}
    for day in forecast_deposit(scenario).days:
        if day.day <= through and day.day in payout_dates:
            month = day.day.replace(day=1)
            planned[month] = planned.get(month, 0) + day.payout_minor
    recorded = {row.month: row.totals.income_minor for row in actual.months}
    return DepositPlanFact(
        period,
        currency,
        tuple(
            DepositInterestMonth(month, planned.get(month, 0), recorded.get(month, 0))
            for month in sorted(planned.keys() | recorded.keys())
        ),
        report_sources(actual, currency, category_id=category.id) if actual.totals else (),
    )
