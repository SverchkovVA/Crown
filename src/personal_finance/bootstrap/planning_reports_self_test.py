"""Синтетическая самопроверка бюджета и состояния счетов без записи книги."""

import hashlib
from datetime import date

from PySide6.QtCore import QCoreApplication, QEvent

from personal_finance.application.analytics import Analytics
from personal_finance.application.vault import VaultSession
from personal_finance.domain.analytics import ReportPeriod
from personal_finance.domain.models import Money
from personal_finance.presentation.account_position import AccountPositionDialog
from personal_finance.presentation.budget import BudgetDialog


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
    budget_dialog = BudgetDialog(budget, accounts, categories)
    position_dialog = AccountPositionDialog(position, categories)
    dialogs = (budget_dialog, position_dialog)
    try:
        if budget_dialog.sources.model().rowCount() != len(
            budget.sources
        ) or position_dialog.accounts.model().rowCount() != len(accounts):
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
        raise RuntimeError("Проверка бюджета или состояния счетов изменила книгу.")
