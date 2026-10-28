"""Согласованный снимок текущего журнала для отчёта без изменения книги."""

from collections.abc import Callable
from threading import RLock

from personal_finance.application.ledger import Ledger
from personal_finance.domain.analytics import (
    IncomeExpenseReport,
    ReportPeriod,
    income_expense_report,
)


class Analytics:
    def __init__(self, ledger: Ledger, mutex: RLock, ensure_open: Callable[[], None]) -> None:
        self._ledger, self._mutex, self._ensure_open = ledger, mutex, ensure_open

    def report(self, period: ReportPeriod) -> IncomeExpenseReport:
        with self._mutex:
            self._ensure_open()
            operations = tuple(self._ledger.operations())
        return income_expense_report(operations, period)
