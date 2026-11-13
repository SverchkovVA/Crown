from PySide6.QtCore import QDate

from personal_finance.domain.ledger import OperationDraft, OperationKind
from personal_finance.domain.models import AccountKind, Money
from personal_finance.presentation.financial_assets import FinancialAssetsDialog
from personal_finance.presentation.investment_portfolio import (
    InvestmentPortfolioDialog,
    InvestmentPortfolioOptions,
)
from personal_finance.presentation.main_window import MainWindow
from tests.gui.test_planning_scenarios_workflow import current
from tests.unit.test_investment_positions import inputs
from tests.unit.test_investments import TODAY


def test_assets_reports_ledger_cash_and_portfolio_values_at_same_date(session, qtbot):
    account = session.catalog.add_account("Учебный инвестсчёт", AccountKind.INVESTMENT)
    cash = session.catalog.add_account("Учебные наличные", AccountKind.CASH)
    session.ledger.add(OperationDraft(OperationKind.OPENING, TODAY, account.id, Money(1000)))
    session.ledger.add(OperationDraft(OperationKind.OPENING, TODAY, cash.id, Money(4000)))
    session.investment_positions.save(inputs(account))
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
        portfolio.show_assets.click()
        dialog = current(window, FinancialAssetsDialog)
        model = dialog.totals.model()
        assert [model.item(0, i).text() for i in range(model.columnCount())] == [
            "RUB",
            "40,00",
            "10,00",
            "11,10",
            "0",
            "61,10",
        ]
        window._report_forms.back.click()
        assert current(window, InvestmentPortfolioDialog) is portfolio
        assert (session.ledger.operations(), session.ledger.balances()) == before
    finally:
        window.lock_book()
