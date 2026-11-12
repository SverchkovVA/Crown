"""Синтетическая проверка отчётов и расчётов планирования без записи книги."""

import hashlib
from datetime import date

from PySide6.QtCore import QCoreApplication, QEvent

from personal_finance.application.analytics import Analytics
from personal_finance.application.vault import VaultSession
from personal_finance.domain.analytics import ReportPeriod
from personal_finance.domain.balance_forecast import PaymentMatch, check_reserve, match_payments
from personal_finance.domain.models import Money
from personal_finance.domain.recurring_payments import (
    PaymentDirection,
    PaymentSchedule,
    RepeatUnit,
    forecast_payments,
)
from personal_finance.domain.savings_goals import SavingsGoalScenario, project_savings_goal
from personal_finance.presentation.account_position import AccountPositionDialog
from personal_finance.presentation.balance_forecast import BalanceForecastDialog
from personal_finance.presentation.budget import BudgetDialog
from personal_finance.presentation.recurring_payments import PaymentCalendarDialog
from personal_finance.presentation.savings_goals import SavingsGoalDialog


def check_planning_reports(
    session: VaultSession, day: date, expected_balances: dict[str, int], expected_expense: int
) -> None:
    ledger = session.ledger
    if ledger is None:
        raise RuntimeError("Журнал недоступен для проверки отчётов.")
    before = ledger.operations(include_deleted=True)
    paths = [session.root / "data" / name for name in ("ledger.sqlite3", "key.json")]
    hashes = [hashlib.sha256(path.read_bytes()).digest() for path in paths]
    analytics = Analytics(ledger, session.mutex, session.ensure_open)
    accounts, categories = session.catalog.accounts(), session.catalog.categories()
    budget = analytics.budget(ReportPeriod(day, day), Money(5000))
    if (
        budget.spent_minor != expected_expense
        or budget.remaining_minor != 5000 - expected_expense
        or sum(line.totals.expense_minor for line in budget.sources) != expected_expense
    ):
        raise RuntimeError("Бюджет не совпал с принятыми расходами и возвратами.")
    position = analytics.account_position(accounts, date.max)
    if (
        {row.account.id: row.minor for row in position.accounts} != expected_balances
        or sum(row.total_minor for row in position.totals) != sum(expected_balances.values())
        or sum(line.minor for line in position.sources) != sum(expected_balances.values())
    ):
        raise RuntimeError("Состояние счетов не совпало с принятыми остатками.")
    goal = project_savings_goal(
        SavingsGoalScenario(
            date(2026, 1, 31), Money(10000), Money(30000), Money(10000), date(2026, 3, 30)
        )
    )
    if (
        goal.reached_on != date(2026, 3, 31)
        or goal.required_monthly_minor != 20000
        or goal.progress_basis_points != 3333
    ):
        raise RuntimeError("Сценарий цели не совпал с календарными пополнениями.")
    first_payment = date(2026, 1, 31)
    payment_dates = (first_payment, date(2026, 2, 28), date(2026, 3, 31))
    forecast = forecast_payments(
        (
            PaymentSchedule(
                "Синтетическая подписка",
                Money(12345),
                PaymentDirection.EXPENSE,
                first_payment,
                RepeatUnit.MONTHS,
            ),
        ),
        ReportPeriod(first_payment, payment_dates[-1]),
    )
    if (
        tuple(event.occurs_on for event in forecast.events) != payment_dates
        or forecast.totals[0].net_minor != -37035
    ):
        raise RuntimeError("Календарь платежей не совпал с проверочными датами и суммами.")
    account = next(value for value in accounts if expected_balances[value.id] > 0)
    balance = analytics.balance_forecast(
        account,
        ReportPeriod(date.max, date.max),
        (PaymentSchedule("Синтетический расход", Money(900), PaymentDirection.EXPENSE, date.max),),
    )
    if (
        balance.opening_minor != expected_balances[account.id]
        or balance.closing_minor != expected_balances[account.id] - 900
        or balance.booked
    ):
        raise RuntimeError("Прогноз остатка не совпал с журналом и ожидаемым платежом.")
    payment_day = ReportPeriod(day, day)
    booked = analytics.balance_forecast(account, payment_day, ())
    actual = next(row for row in booked.booked if row.minor)
    direction = PaymentDirection.INCOME if actual.minor > 0 else PaymentDirection.EXPENSE
    with_plan = analytics.balance_forecast(
        account,
        payment_day,
        (PaymentSchedule("Синтетическое совпадение", Money(abs(actual.minor)), direction, day),),
    )
    matched = match_payments(with_plan, (PaymentMatch(0, actual.operation.revision_id),))
    if matched.closing_minor != booked.closing_minor or match_payments(matched, ()) != with_plan:
        raise RuntimeError("Явное сопоставление не сняло двойной учёт в прогнозе.")
    reserve = check_reserve(matched, Money(matched.closing_minor + 1))
    if reserve.first_below != day or reserve.shortfall_minor != 1:
        raise RuntimeError("Контроль резерва не совпал с остатком на конец дня.")
    budget_dialog = BudgetDialog(budget, accounts, categories)
    position_dialog = AccountPositionDialog(position, categories)
    goal_dialog = SavingsGoalDialog(goal)
    calendar_dialog = PaymentCalendarDialog(forecast)
    forecast_dialog = BalanceForecastDialog(balance)
    dialogs = (budget_dialog, position_dialog, goal_dialog, calendar_dialog, forecast_dialog)
    try:
        if (
            budget_dialog.sources.model().rowCount() != len(budget.sources)
            or position_dialog.accounts.model().rowCount() != len(accounts)
            or calendar_dialog.table.model().rowCount() != len(payment_dates)
            or forecast_dialog.days.model().rowCount() != 1
        ):
            raise RuntimeError("Учтённые строки недоступны в интерфейсе отчётов.")
    finally:
        for dialog in dialogs:
            dialog.reject()
            dialog.deleteLater()
            QCoreApplication.sendPostedEvents(dialog, QEvent.Type.DeferredDelete)
    if (
        ledger.operations(include_deleted=True) != before
        or [hashlib.sha256(path.read_bytes()).digest() for path in paths] != hashes
    ):
        raise RuntimeError("Проверка отчётов планирования изменила книгу.")
