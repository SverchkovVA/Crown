from PySide6.QtCore import QDate

from personal_finance.domain.ledger import OperationDraft, OperationKind
from personal_finance.domain.models import AccountKind, CategoryKind, Money
from personal_finance.presentation.analytics import OperationSourceDialog
from personal_finance.presentation.analytics_cashflow import CashFlowSourcesDialog
from personal_finance.presentation.investment_cash_reconciliation import InvestmentCashDialog
from personal_finance.presentation.investment_portfolio import (
    InvestmentPortfolioDialog,
    InvestmentPortfolioOptions,
)
from personal_finance.presentation.main_window import MainWindow
from tests.gui.test_planning_scenarios_workflow import current
from tests.unit.test_investment_positions import inputs
from tests.unit.test_investments import TODAY


def test_portfolio_reconciliation_navigates_to_captured_revision(session, qtbot):
    account = session.catalog.add_account("Учебный инвестиционный счёт", AccountKind.INVESTMENT)
    category = session.catalog.add_category("Учебная категория", CategoryKind.EXPENSE)
    session.investment_positions.save(inputs(account))
    draft = OperationDraft(OperationKind.EXPENSE, TODAY, account.id, Money(1001), category.id)
    movement = session.ledger.add(draft)
    before = session.ledger.operations(), session.ledger.balances()
    window = MainWindow()
    qtbot.addWidget(window)
    window.show()
    window.activate_session(session)
    window.navigation.setCurrentRow(2)
    try:
        window.investment_portfolio_action.trigger()
        options = current(window, InvestmentPortfolioOptions)
        options.as_of.setDate(QDate(TODAY.year, TODAY.month, TODAY.day))
        options.calculate.click()
        qtbot.waitUntil(lambda: not window._busy, timeout=15000)
        portfolio = current(window, InvestmentPortfolioDialog)
        portfolio.check_cash.click()
        reconciliation = current(window, InvestmentCashDialog)
        assert not reconciliation.open_sources.isEnabled()
        index = next(i for i, row in enumerate(reconciliation.rows) if row.candidates)
        reconciliation.table.selectRow(index)
        assert reconciliation.table.model().item(index, 5).text() == "Один кандидат"
        reconciliation.open_sources.click()
        sources = current(window, CashFlowSourcesDialog)
        sources.details_button.click()
        operation = current(window, OperationSourceDialog)
        assert operation.flow.operation.revision_id == movement.revision_id
        window._report_forms.back.click()
        assert current(window, CashFlowSourcesDialog) is sources
        assert (session.ledger.operations(), session.ledger.balances()) == before
    finally:
        window.lock_book()
