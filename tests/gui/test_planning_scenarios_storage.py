"""Работа с условиями бюджета и цели через отчёты настоящей SQLCipher-книги."""

from datetime import date

import pytest
from PySide6.QtCore import QDate
from PySide6.QtWidgets import QPushButton

from personal_finance.domain.analytics import ReportPeriod
from personal_finance.domain.models import AccountKind, Money
from personal_finance.domain.planning_scenarios import BudgetScenario, PlanningKind
from personal_finance.presentation.budget import BudgetDialog
from personal_finance.presentation.main_window import MainWindow
from personal_finance.presentation.planning_options import (
    BudgetScenarioOptions,
    SavingsGoalScenarioOptions,
)
from personal_finance.presentation.planning_scenarios import PlanningAction, SavedPlanningDialog
from personal_finance.presentation.planning_scenarios_workflow import PlanningNameDialog
from personal_finance.presentation.savings_goals import SavingsGoalDialog
from tests.gui.test_planning_scenarios_workflow import current
from tests.integration.test_deposit_scenario_upgrade import create_at
from tests.unit.test_planning_scenarios import goal


@pytest.mark.parametrize("kind", [PlanningKind.BUDGET, PlanningKind.SAVINGS_GOAL])
def test_embedded_recalculate_then_save_preserves_conditions_and_journal(session, qtbot, kind):
    repo = session.planning_scenarios
    assert repo is not None
    session.catalog.add_account("Синтетический счёт", AccountKind.CASH)
    window = MainWindow()
    qtbot.addWidget(window)
    window.show()
    window.activate_session(session)
    window.navigation.setCurrentRow(2)
    before = session.ledger.operations(), session.ledger.balances()
    try:
        entry = next(b for b in window.report_buttons if b.text() == "Сохранённые бюджеты и цели…")
        assert entry.isVisible() and entry.isEnabled()
        entry.click()
        qtbot.waitUntil(lambda: not window._busy)
        listing = current(window, SavedPlanningDialog)
        listing.kind.setCurrentIndex(listing.kind.findData(kind.value))
        assert listing.kind.currentData() == kind.value
        listing.buttons[PlanningAction.CREATE].click()
        qtbot.waitUntil(lambda: not window._busy)
        if kind is PlanningKind.BUDGET:
            form = current(window, BudgetScenarioOptions)
            form.period_mode.setCurrentIndex(1)
            form.start.setDate(QDate(2026, 10, 31))
            form.end.setDate(QDate(2026, 11, 1))
            form.limit.setText("500,00")
            expected = BudgetScenario(
                ReportPeriod(date(2026, 10, 31), date(2026, 11, 1)),
                Money(50000),
            )
            result_type = BudgetDialog
        else:
            form = current(window, SavingsGoalScenarioOptions)
            form.as_of.setDate(QDate(2026, 1, 31))
            form.current.setText("100,00")
            form.target.setText("300,00")
            form.monthly.setText("100,00")
            form.deadline.setDate(QDate(2026, 3, 30))
            expected = goal()
            result_type = SavingsGoalDialog
        form.calculate.click()
        qtbot.waitUntil(lambda: not window._busy)
        preview = current(window, result_type)
        assert repo.items() == []
        preview.reject()
        assert current(window, type(form)) is form
        form.calculate.click()
        qtbot.waitUntil(lambda: not window._busy)
        preview = current(window, result_type)
        save = next(
            b for b in preview.findChildren(QPushButton) if b.text() == "Сохранить условия…"
        )
        save.click()
        name = current(window, PlanningNameDialog)
        name.name.setText("Условия для проверки")
        name._accept()
        qtbot.waitUntil(
            lambda: (
                not window._busy
                and isinstance(
                    window._report_forms.stack.currentWidget().widget(),
                    SavedPlanningDialog,
                )
            )
        )
        record = repo.items(kind=kind)[0]
        assert record.scenario == expected and record.name == "Условия для проверки"
        assert (session.ledger.operations(), session.ledger.balances()) == before
        session.check_integrity()
    finally:
        window._report_forms.clear()
        window.session = None


def test_reports_keep_calculators_available_in_existing_0014(qtbot, tmp_path, monkeypatch):
    session, _ = create_at(monkeypatch, tmp_path / "old.pfvault", "0014")
    window = MainWindow()
    qtbot.addWidget(window)
    try:
        window.activate_session(session)
        window.navigation.setCurrentRow(2)
        assert not window.saved_planning_action.isEnabled()
        assert window.budget_action.isEnabled() and window.savings_goal_action.isEnabled()
        window.request_saved_planning()
        assert window._report_forms.stack.count() == 0
        assert session.schema_revision == "0014" and session.planning_scenarios is None
    finally:
        window.session = None
        session.close()
