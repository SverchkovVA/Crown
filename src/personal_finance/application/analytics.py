"""Согласованный снимок текущего журнала для отчёта без изменения книги."""

from collections.abc import Callable
from threading import RLock

from personal_finance.application.ledger import Ledger
from personal_finance.domain.analytics import (
    IncomeExpenseReport,
    ReportPeriod,
    ReportScope,
    income_expense_report,
)
from personal_finance.domain.analytics_comparison import IncomeExpenseComparison, compare_periods
from personal_finance.domain.ledger import Operation


class Analytics:
    def __init__(self, ledger: Ledger, mutex: RLock, ensure_open: Callable[[], None]) -> None:
        self._ledger, self._mutex, self._ensure_open = ledger, mutex, ensure_open

    def report(self, period: ReportPeriod, scope: ReportScope | None = None) -> IncomeExpenseReport:
        return income_expense_report(self._snapshot(), period, scope)

    def compare(
        self, before: ReportPeriod, after: ReportPeriod, scope: ReportScope | None = None
    ) -> IncomeExpenseComparison:
        return compare_periods(self._snapshot(), before, after, scope)

    def _snapshot(self) -> tuple[Operation, ...]:
        with self._mutex:
            self._ensure_open()
            return tuple(self._ledger.operations())
