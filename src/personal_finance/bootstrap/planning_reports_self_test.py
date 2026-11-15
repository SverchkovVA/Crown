"""Синтетическая проверка отчётов и расчётов планирования без записи книги."""

import hashlib
from dataclasses import dataclass, replace
from datetime import date, timedelta
from decimal import Decimal
from uuid import uuid4

from PySide6.QtCore import QCoreApplication, QEvent
from PySide6.QtWidgets import QTabWidget

from personal_finance.application.analytics import Analytics
from personal_finance.application.vault import VaultSession
from personal_finance.domain.analytics import ReportPeriod
from personal_finance.domain.balance_forecast import PaymentMatch, check_reserve, match_payments
from personal_finance.domain.investment_portfolio import report_saved_portfolio
from personal_finance.domain.investment_positions import (
    InvestmentPositionInputs,
    SavedInvestmentPosition,
)
from personal_finance.domain.investment_prices import ManualPrice, PriceUnit, value_investments
from personal_finance.domain.investments import (
    BondAmortization,
    Security,
    SecurityKind,
    SecurityTrade,
    StockSplit,
    TradeKind,
    project_investments,
)
from personal_finance.domain.models import RUB, Account, AccountKind, Money
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
from personal_finance.presentation.investment_portfolio import InvestmentPortfolioDialog
from personal_finance.presentation.investment_preview import (
    InvestmentPreviewDialog,
    InvestmentPreviewRequest,
)
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
    investment = _investment_preview(day)
    split_view, repayment_view = _corporate_event_previews(day)
    dialogs = (
        budget_dialog,
        position_dialog,
        goal_dialog,
        calendar_dialog,
        forecast_dialog,
        investment,
        split_view,
        repayment_view,
    )
    try:
        if (
            budget_dialog.sources.model().rowCount() != len(budget.sources)
            or position_dialog.accounts.model().rowCount() != len(accounts)
            or calendar_dialog.table.model().rowCount() != len(payment_dates)
            or forecast_dialog.days.model().rowCount() != 1
        ):
            raise RuntimeError("Учтённые строки недоступны в интерфейсе отчётов.")
        split_tabs = split_view.findChild(QTabWidget)
        if (
            split_tabs is None
            or split_tabs.tabText(split_tabs.count() - 1) != "Дробление акций"
            or repayment_view.amortizations.model().rowCount() != 1
            or repayment_view.amortizations.model().index(0, 3).data() != "750,00"
        ):
            raise RuntimeError("Корпоративное событие недоступно в собранном отчёте.")
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


def _investment_inputs(account: Account, day: date) -> InvestmentPositionInputs:
    security = Security(str(uuid4()), "TEST", "Синтетическая акция", SecurityKind.STOCK)
    events = tuple(
        SecurityTrade(
            str(uuid4()),
            account.id,
            security.id,
            day,
            order,
            kind,
            quantity,
            Money(amount),
            Money(fee),
        )
        for order, kind, quantity, amount, fee in (
            (1, TradeKind.BUY, 3, 1000, 1),
            (2, TradeKind.BUY, 2, 800, 2),
            (3, TradeKind.SELL, 4, 1600, 10),
        )
    )
    quote = ManualPrice(security.id, day, PriceUnit.CASH_PER_SECURITY, Decimal("5.55"), RUB)
    return InvestmentPositionInputs(account.id, security, events, (quote,))


def _investment_preview(day: date) -> InvestmentPreviewDialog:
    account = Account(
        str(uuid4()), "Синтетический инвестиционный счёт", AccountKind.INVESTMENT, None
    )
    inputs = _investment_inputs(account, day)
    security, events = inputs.security, inputs.events
    request = InvestmentPreviewRequest(account, security, events, day, inputs.prices)
    portfolio = project_investments((account,), (security,), events, day)
    result = value_investments(portfolio, request.prices)
    (row,) = result.positions
    if (
        row.market_minor != 555
        or row.position.quantity != 1
        or row.position.cost_minor != 401
        or row.position.realized_minor != 188
    ):
        raise RuntimeError("Инвестиционная позиция не совпала с проверочными партиями FIFO.")
    dialog = InvestmentPreviewDialog(request, result)
    if (
        dialog.lots.model().rowCount() != 1
        or dialog.allocations.model().rowCount() != 2
        or dialog.performance_label is None
        or "18,97%" not in dialog.performance_label.text()
    ):
        raise RuntimeError("Партии FIFO недоступны в интерфейсе инвестиционной позиции.")
    return dialog


def _corporate_examples(account: Account, day: date) -> tuple[InvestmentPositionInputs, ...]:
    base = _investment_inputs(account, day)
    split = StockSplit(str(uuid4()), account.id, base.security.id, day, 4, 3)
    old_price = replace(base.prices[0], quoted_on=day - timedelta(days=1))
    shares = replace(base, prices=(old_price,), splits=(split,))

    bond = Security(str(uuid4()), "BOND-TEST", "Синтетическая облигация", SecurityKind.BOND)
    purchase = SecurityTrade(
        str(uuid4()),
        account.id,
        bond.id,
        day,
        1,
        TradeKind.BUY,
        3,
        Money(270000),
        Money(9),
        Money(9000),
    )
    amortization = BondAmortization(
        str(uuid4()), account.id, bond.id, day, 2, 3, Money(100000), Money(75000)
    )
    obsolete = ManualPrice(
        bond.id, day, PriceUnit.PERCENT_OF_FACE, Decimal("99.2"), RUB, Money(100000), Money(0)
    )
    bonds = InvestmentPositionInputs(
        account.id, bond, (purchase,), (obsolete,), amortizations=(amortization,)
    )
    return shares, bonds


def _corporate_event_previews(day: date) -> tuple[InvestmentPreviewDialog, InvestmentPreviewDialog]:
    account = Account(str(uuid4()), "Синтетический счёт", AccountKind.INVESTMENT, None)
    dialogs = []
    for inputs in _corporate_examples(account, day):
        request = InvestmentPreviewRequest(
            account,
            inputs.security,
            inputs.events,
            day,
            inputs.prices,
            inputs.splits,
            inputs.amortizations,
        )
        portfolio = project_investments(
            (account,),
            (inputs.security,),
            inputs.events,
            day,
            splits=inputs.splits,
            amortizations=inputs.amortizations,
        )
        valuation = value_investments(portfolio, inputs.prices)
        (holding,) = valuation.positions
        expected_cost = 401 if inputs.splits else 211507
        if (holding.position.quantity, holding.position.cost_minor, holding.price) != (
            3,
            expected_cost,
            None,
        ):
            raise RuntimeError(
                "Корпоративное событие не прошло расчёт количества, стоимости и цены."
            )
        if inputs.amortizations:
            (payment,) = portfolio.amortizations
            if (payment.realized_minor, payment.event.principal_minor) != (7498, 75000):
                raise RuntimeError("Не совпали выплата номинала и результат амортизации.")
        dialogs.append(InvestmentPreviewDialog(request, valuation))
    split, amortization = dialogs
    return split, amortization


@dataclass(frozen=True, slots=True)
class StoredInvestmentExample:
    initial: SavedInvestmentPosition
    latest: SavedInvestmentPosition
    initial_command: str
    latest_command: str


def store_investment_examples(
    session: VaultSession, day: date
) -> tuple[StoredInvestmentExample, ...]:
    storage = session.investment_positions
    if storage is None or not session.supports_corporate_events:
        raise RuntimeError("Новая книга не поддерживает сохранение корпоративных событий.")
    account = session.catalog.add_account("Синтетический инвестсчёт", AccountKind.INVESTMENT)
    examples = []
    for inputs in _corporate_examples(account, day):
        initial_command, latest_command = str(uuid4()), str(uuid4())
        initial = storage.save(
            replace(inputs, splits=(), amortizations=()), command_id=initial_command
        )
        latest = storage.save(
            inputs,
            identifier=initial.id,
            expected_revision_id=initial.revision_id,
            command_id=latest_command,
        )
        examples.append(StoredInvestmentExample(initial, latest, initial_command, latest_command))
    return tuple(examples)


def check_investment_examples(
    session: VaultSession, day: date, examples: tuple[StoredInvestmentExample, ...]
) -> None:
    storage = session.investment_positions
    if storage is None or not session.supports_corporate_events:
        raise RuntimeError("В книге недоступны сохранённые корпоративные события.")
    records = storage.items()
    if {item.id: item for item in records} != {
        example.latest.id: example.latest for example in examples
    }:
        raise RuntimeError("Не совпали действующие версии позиций.")
    for example in examples:
        first, latest = example.initial, example.latest
        if (
            storage.history(first.id) != [first, latest]
            or storage.save(first.inputs, command_id=example.initial_command) != first
            or storage.save(
                latest.inputs,
                identifier=first.id,
                expected_revision_id=first.revision_id,
                command_id=example.latest_command,
            )
            != latest
        ):
            raise RuntimeError("Версии инвестиционной позиции или квитанции не сохранились.")
    report = report_saved_portfolio(session.catalog.accounts(), records, day)
    amounts = {
        row.position.security.kind: (
            row.position.quantity,
            row.position.cost_minor,
            row.position.realized_minor,
        )
        for row in report.rows
    }
    (total,) = report.totals
    if (
        amounts
        != {
            SecurityKind.STOCK: (3, 401, 188),
            SecurityKind.BOND: (3, 211507, 7498),
        }
        or total.unpriced_count != 2
        or total.priced_market_minor
    ):
        raise RuntimeError("Итог портфеля не совпал с сохранёнными событиями.")
    dialog = InvestmentPortfolioDialog(report)
    try:
        if dialog.positions.model().rowCount() != 2:
            raise RuntimeError("Сохранённые позиции недоступны в интерфейсе портфеля.")
    finally:
        dialog.reject()
        dialog.deleteLater()
        QCoreApplication.sendPostedEvents(dialog, QEvent.Type.DeferredDelete)
