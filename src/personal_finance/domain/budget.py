"""Исполнение лимита за выбранный период по существующим проводкам."""

from collections.abc import Sequence
from dataclasses import dataclass

from personal_finance.domain.analytics import (
    OperationFlow,
    ReportPeriod,
    ReportScope,
    income_expense_report,
    report_sources,
)
from personal_finance.domain.errors import ValidationError
from personal_finance.domain.ledger import Operation
from personal_finance.domain.models import Money
from personal_finance.domain.periods import AccountingMonth


@dataclass(frozen=True, slots=True)
class BudgetExecution:
    period: ReportPeriod
    limit: Money
    scope: ReportScope
    sources: tuple[OperationFlow, ...]

    @property
    def spent_minor(self) -> int:
        return sum(row.totals.expense_minor for row in self.sources)

    @property
    def remaining_minor(self) -> int:
        return self.limit.minor - self.spent_minor


def budget_execution(
    operations: Sequence[Operation],
    period: ReportPeriod,
    limit: Money,
    scope: ReportScope | None = None,
) -> BudgetExecution:
    if not isinstance(limit, Money) or limit.minor < 0:
        raise ValidationError("Введите неотрицательный лимит расходов.")
    report = income_expense_report(operations, period, scope)
    currencies = {totals.currency for totals in report.totals}
    if any(
        currency.code == limit.currency.code and currency != limit.currency
        for currency in currencies
    ):
        raise ValidationError("Определение валюты лимита отличается от валюты журнала.")
    sources = (
        tuple(row for row in report_sources(report, limit.currency) if row.totals.expense_minor)
        if limit.currency in currencies
        else ()
    )
    return BudgetExecution(period, limit, report.scope, sources)


def monthly_budget(
    operations: Sequence[Operation],
    month: AccountingMonth,
    limit: Money,
    scope: ReportScope | None = None,
) -> BudgetExecution:
    if not isinstance(month, AccountingMonth):
        raise ValidationError("Укажите месяц бюджета.")
    return budget_execution(operations, ReportPeriod(month.start, month.end), limit, scope)
