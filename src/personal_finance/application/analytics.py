"""Согласованный снимок текущего журнала для отчёта без изменения книги."""

from collections.abc import Callable
from datetime import date
from threading import RLock

from personal_finance.application.ledger import Ledger
from personal_finance.domain.account_position import AccountPosition, account_position
from personal_finance.domain.analytics import (
    IncomeExpenseReport,
    ReportPeriod,
    ReportScope,
    income_expense_report,
)
from personal_finance.domain.analytics_cashflow import CashFlowReport, cash_flow_report
from personal_finance.domain.analytics_comparison import IncomeExpenseComparison, compare_periods
from personal_finance.domain.budget import BudgetExecution, budget_execution, monthly_budget
from personal_finance.domain.deposit_plan_fact import DepositPlanFact, deposit_plan_fact
from personal_finance.domain.deposits import DepositScenario
from personal_finance.domain.ledger import Operation
from personal_finance.domain.models import Account, Category, Money
from personal_finance.domain.periods import AccountingMonth


class Analytics:
    def __init__(self, ledger: Ledger, mutex: RLock, ensure_open: Callable[[], None]) -> None:
        self._ledger, self._mutex, self._ensure_open = ledger, mutex, ensure_open

    def report(self, period: ReportPeriod, scope: ReportScope | None = None) -> IncomeExpenseReport:
        return income_expense_report(self._snapshot(), period, scope)

    def compare(
        self, before: ReportPeriod, after: ReportPeriod, scope: ReportScope | None = None
    ) -> IncomeExpenseComparison:
        return compare_periods(self._snapshot(), before, after, scope)

    def cash_flow(self, period: ReportPeriod, scope: ReportScope | None = None) -> CashFlowReport:
        return cash_flow_report(self._snapshot(), period, scope)

    def deposit_plan_fact(
        self, scenario: DepositScenario, account: Account, category: Category, through: date
    ) -> DepositPlanFact:
        return deposit_plan_fact(scenario, self._snapshot(), account, category, through)

    def account_position(self, accounts: list[Account], as_of: date) -> AccountPosition:
        return account_position(self._snapshot(), accounts, as_of)

    def budget(
        self, period: ReportPeriod, limit: Money, scope: ReportScope | None = None
    ) -> BudgetExecution:
        return budget_execution(self._snapshot(), period, limit, scope)

    def monthly_budget(
        self, month: AccountingMonth, limit: Money, scope: ReportScope | None = None
    ) -> BudgetExecution:
        return monthly_budget(self._snapshot(), month, limit, scope)

    def _snapshot(self) -> tuple[Operation, ...]:
        with self._mutex:
            self._ensure_open()
            return tuple(self._ledger.operations())
