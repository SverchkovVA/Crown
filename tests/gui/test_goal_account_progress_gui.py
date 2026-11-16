"""Расчёт сохранённой цели из журнала через штатный раздел отчётов."""

from dataclasses import replace
from datetime import timedelta

from PySide6.QtWidgets import QPushButton

from personal_finance.domain.ledger import OperationDraft, OperationKind
from personal_finance.domain.models import AccountKind, CategoryKind, Money
from personal_finance.domain.planning_scenarios import PlanningKind
from personal_finance.presentation.analytics import OperationSourceDialog
from personal_finance.presentation.analytics_cashflow import CashFlowSourcesDialog
from personal_finance.presentation.main_window import MainWindow
from personal_finance.presentation.planning_scenarios import PlanningAction, SavedPlanningDialog
from personal_finance.presentation.savings_goals import (
    SavingsGoalAccountOptions,
    SavingsGoalDialog,
    SavingsGoalHistoryDialog,
)
from tests.gui.test_planning_scenarios_workflow import current
from tests.unit.test_planning_scenarios import goal


def action(form, text):
    return next(button for button in form.findChildren(QPushButton) if button.text() == text)


def test_saved_goal_uses_only_selected_account_at_date_and_shows_snapshot(session, qtbot):
    scenario = goal()
    saved = session.planning_scenarios.save("Учебная цель", scenario)
    ledger = session.ledger
    catalog = session.catalog
    wallet = catalog.add_account("Учебный кошелёк", AccountKind.CASH)
    account = catalog.add_account("Для учебной цели", AccountKind.SAVINGS)
    debit = catalog.add_account("Учебный долг", AccountKind.CASH)
    fee = catalog.add_category("Учебная комиссия", CategoryKind.EXPENSE)
    earlier = scenario.as_of - timedelta(days=1)
    later = scenario.as_of + timedelta(days=1)
    opening = ledger.add(OperationDraft(OperationKind.OPENING, earlier, account.id, Money(16000)))
    ledger.add(OperationDraft(OperationKind.OPENING, earlier, wallet.id, Money(100000)))
    ledger.add(OperationDraft(OperationKind.OPENING, earlier, debit.id, Money(-100)))
    ledger.add(
        OperationDraft(
            OperationKind.TRANSFER,
            scenario.as_of,
            wallet.id,
            Money(10000),
            destination_account_id=account.id,
        )
    )
    ledger.add(
        OperationDraft(
            OperationKind.TRANSFER,
            scenario.as_of,
            account.id,
            Money(3000),
            destination_account_id=wallet.id,
            fee=Money(100),
            fee_category_id=fee.id,
        )
    )
    deleted = ledger.add(
        OperationDraft(
            OperationKind.EXPENSE,
            earlier,
            account.id,
            Money(500),
            category_id=fee.id,
        )
    )
    ledger.delete(deleted.id, deleted.revision_id)
    ledger.add(
        OperationDraft(
            OperationKind.EXPENSE,
            later,
            account.id,
            Money(300),
            category_id=fee.id,
        )
    )
    ledger.add(
        OperationDraft(
            OperationKind.EXPENSE,
            earlier,
            account.id,
            Money(2000),
            category_id=fee.id,
        )
    )
    before = ledger.operations(include_deleted=True)
    window = MainWindow()
    qtbot.addWidget(window)
    window.show()
    window.activate_session(session)
    window.navigation.setCurrentRow(2)
    try:
        action(window, "Сохранённые бюджеты и цели…").click()
        qtbot.waitUntil(lambda: not window._busy)
        listing = current(window, SavedPlanningDialog)
        listing.kind.setCurrentIndex(listing.kind.findData(PlanningKind.SAVINGS_GOAL.value))
        listing.table.selectRow(0)
        listing.buttons[PlanningAction.CALCULATE].click()
        qtbot.waitUntil(lambda: not window._busy)
        manual = current(window, SavingsGoalDialog)
        assert manual.fields["Прогресс"].text() == "33,33 %"
        action(manual, "По остатку счёта…").click()
        qtbot.waitUntil(lambda: not window._busy)
        options = current(window, SavingsGoalAccountOptions)
        assert not options.calculate.isEnabled()
        options.account.setCurrentIndex(options.account.findData(debit.id))
        options.calculate.click()
        qtbot.waitUntil(lambda: not window._busy)
        assert current(window, SavingsGoalAccountOptions) is options
        assert "отрицательный остаток" in options.feedback.text()
        options.account.setCurrentIndex(options.account.findData(account.id))
        options.calculate.click()
        qtbot.waitUntil(lambda: not window._busy)
        actual = current(window, SavingsGoalDialog)
        assert actual.fields["Прогресс"].text() == "69,66 %"
        assert actual.projection.scenario.current == Money(20900)
        assert ledger.operations(include_deleted=True) == before
        assert session.planning_scenarios.items(kind=PlanningKind.SAVINGS_GOAL) == [saved]

        ledger.replace(opening.id, opening.revision_id, replace(opening.draft, amount=Money(300)))
        action(actual, "История прогресса").click()
        qtbot.waitUntil(lambda: not window._busy)
        history = current(window, SavingsGoalHistoryDialog)
        assert [
            (row.day, row.balance_minor, row.progress_basis_points) for row in history.days
        ] == [
            (scenario.as_of, 20900, 6966),
            (earlier, 14000, 4666),
        ]
        history.table.selectRow(1)
        history.sources.click()
        day_sources = current(window, CashFlowSourcesDialog)
        assert {line.operation.draft.occurred_on for line in day_sources.lines} == {earlier}
        assert {line.operation.revision_id for line in day_sources.lines} >= {opening.revision_id}
        day_sources.reject()
        history.reject()
        assert current(window, SavingsGoalDialog) is actual

        action(actual, "Движения счёта").click()
        sources = current(window, CashFlowSourcesDialog)
        assert len(sources.lines) == 4
        assert sum(line.minor for line in sources.lines) == 20900
        assert all(line.account_id == account.id for line in sources.lines)
        row = next(i for i, line in enumerate(sources.lines) if line.operation.id == opening.id)
        sources.table.selectRow(row)
        sources.details_button.click()
        detail = current(window, OperationSourceDialog)
        assert detail.flow.operation == opening
    finally:
        window.lock_book()
