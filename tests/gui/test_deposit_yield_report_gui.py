from dataclasses import replace
from datetime import timedelta

from PySide6.QtCore import QDate
from PySide6.QtWidgets import QLabel, QTabWidget

from personal_finance.domain.ledger import OperationDraft, OperationKind
from personal_finance.domain.models import AccountKind, CategoryKind, Money
from personal_finance.presentation.analytics import OperationSourceDialog
from personal_finance.presentation.deposit_plan_fact import (
    DepositPlanFactDialog,
    DepositPlanFactOptions,
)
from personal_finance.presentation.deposit_scenarios import SavedScenariosDialog, ScenarioAction
from personal_finance.presentation.main_window import MainWindow
from tests.gui.test_analytics_gui import fingerprints
from tests.gui.test_planning_scenarios_workflow import current
from tests.unit.test_deposit_plan_fact import OPENING, comparison_scenario


def test_dedicated_account_yield_through_normal_gui_with_snapshot_details(qtbot, session):
    account = session.catalog.add_account("Учебный вклад", AccountKind.SAVINGS)
    interest = session.catalog.add_category("Зачисленные проценты", CategoryKind.INCOME)
    through = OPENING + timedelta(days=365)
    scenario = replace(comparison_scenario(), matures_on=through, payout_dates=(through,))
    session.deposit_scenarios.save("Вклад для проверки", scenario)
    opened = session.ledger.add(
        OperationDraft(OperationKind.OPENING, OPENING, account.id, Money(100000))
    )
    session.ledger.add(
        OperationDraft(OperationKind.INCOME, through, account.id, Money(10000), interest.id)
    )
    before = fingerprints(session)

    window = MainWindow()
    qtbot.addWidget(window)
    window.activate_session(session)
    window.navigation.setCurrentRow(2)
    window.show()
    try:
        window.report_buttons[-1].click()
        qtbot.waitUntil(lambda: not window._busy, timeout=15000)
        listing = current(window, SavedScenariosDialog)
        listing.table.selectRow(0)
        listing.buttons[ScenarioAction.PLAN_FACT].click()
        qtbot.waitUntil(lambda: not window._busy, timeout=15000)
        options = current(window, DepositPlanFactOptions)
        assert not options.dedicated_account.isChecked()
        options.account.setCurrentIndex(options.account.findData(account.id))
        options.category.setCurrentIndex(options.category.findData(interest.id))
        options.through.setDate(QDate(through.year, through.month, through.day))
        options.dedicated_account.setChecked(True)
        options.compare.click()
        qtbot.waitUntil(lambda: not window._busy, timeout=15000)
        view = current(window, DepositPlanFactDialog)
        labels = [label.text() for label in view.findChildren(QLabel)]
        assert "10,00 %" in labels
        assert "1 100,00 RUB" in labels
        view.findChild(QTabWidget).setCurrentIndex(2)
        model = view.yield_sources.model()
        assert model.rowCount() == 2
        view.yield_sources.selectRow(0)
        view.yield_sources.doubleClicked.emit(model.index(0, 0))
        details = current(window, OperationSourceDialog)
        assert details.flow.operation.revision_id == opened.revision_id
        assert fingerprints(session) == before
    finally:
        window.lock_book()
