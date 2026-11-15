"""Сравнение запланированных процентов с выбранным доходом журнала."""

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from itertools import groupby

from personal_finance.domain.analytics import (
    OperationFlow,
    ReportPeriod,
    ReportScope,
    income_expense_report,
    report_sources,
)
from personal_finance.domain.analytics_cashflow import CashMovement, cash_flow_report
from personal_finance.domain.deposit_yield import effective_deposit_yield
from personal_finance.domain.deposits import DepositScenario, forecast_deposit
from personal_finance.domain.errors import ValidationError
from personal_finance.domain.ledger import Operation, OperationKind
from personal_finance.domain.models import Account, Category, CategoryKind, Currency, Money


@dataclass(frozen=True, slots=True)
class DepositInterestMonth:
    month: date
    planned_minor: int
    actual_minor: int

    @property
    def difference_minor(self) -> int:
        return self.actual_minor - self.planned_minor


@dataclass(frozen=True, slots=True)
class ActualDepositYield:
    closing_minor: int
    movements: tuple[CashMovement, ...]
    annual_percent: Decimal | None
    unavailable_reason: str = ""


@dataclass(frozen=True, slots=True)
class DepositPlanFact:
    period: ReportPeriod
    currency: Currency
    months: tuple[DepositInterestMonth, ...]
    sources: tuple[OperationFlow, ...]
    actual_yield: ActualDepositYield | None = None

    @property
    def planned_minor(self) -> int:
        return sum(row.planned_minor for row in self.months)

    @property
    def actual_minor(self) -> int:
        return sum(row.actual_minor for row in self.months)

    @property
    def difference_minor(self) -> int:
        return self.actual_minor - self.planned_minor


def _yield_from_ledger(
    period: ReportPeriod,
    account: Account,
    currency: Currency,
    operations: Sequence[Operation],
    sources: tuple[OperationFlow, ...],
) -> ActualDepositYield:
    cash = cash_flow_report(operations, period, ReportScope(frozenset((account.id,))))
    movements = tuple(
        row for row in cash.movements if row.operation.draft.occurred_on >= period.start
    )
    opening = sum(row.totals.opening_minor for row in cash.accounts)
    closing = sum(row.totals.closing_minor for row in cash.accounts)
    interest = {source.operation.id: source.totals.income_minor for source in sources}

    try:
        if opening:
            raise ValidationError("Перед датой открытия на счёте уже были средства.")
        if any(row.totals.currency != currency for row in cash.accounts):
            raise ValidationError("Движения счёта записаны в другой валюте.")
        flows: list[tuple[date, Money]] = []
        balance = 0
        for day, date_movements in groupby(
            movements, key=lambda row: row.operation.draft.occurred_on
        ):
            for row in date_movements:
                draft = row.operation.draft
                balance += row.minor
                if draft.kind is OperationKind.OPENING and day == period.start and row.minor > 0:
                    flows.append((day, Money(-row.minor, currency)))
                elif draft.kind is OperationKind.TRANSFER:
                    if draft.account_id == account.id:
                        from_investor = draft.amount.minor
                    else:
                        from_investor = -draft.amount.minor - draft.fee.minor
                    flows.append((day, Money(from_investor, currency)))
                elif not (
                    draft.kind is OperationKind.INCOME
                    and interest.get(row.operation.id) == draft.amount.minor
                ):
                    raise ValidationError(
                        "В журнале есть движения вне пополнений, выплат и выбранных процентов."
                    )
            if balance < 0:
                raise ValidationError("На одну из дат остаток счёта отрицательный.")
        if closing:
            flows.append((period.end, Money(closing, currency)))
        annual = effective_deposit_yield(flows)
    except ValidationError as error:
        return ActualDepositYield(closing, movements, None, str(error))
    return ActualDepositYield(closing, movements, annual)


def deposit_plan_fact(
    scenario: DepositScenario,
    operations: Sequence[Operation],
    account: Account,
    category: Category,
    through: date,
    *,
    dedicated_account: bool = False,
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
    sources = report_sources(actual, currency, category_id=category.id) if actual.totals else ()
    return DepositPlanFact(
        period,
        currency,
        tuple(
            DepositInterestMonth(month, planned.get(month, 0), recorded.get(month, 0))
            for month in sorted(planned.keys() | recorded.keys())
        ),
        sources,
        _yield_from_ledger(period, account, currency, operations, sources)
        if dedicated_account
        else None,
    )
