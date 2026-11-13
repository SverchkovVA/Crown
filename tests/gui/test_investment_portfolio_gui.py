from dataclasses import replace
from decimal import Decimal

from PySide6.QtCore import QDate

from personal_finance.application.vault import create_vault
from personal_finance.domain.investment_prices import ManualPrice, PriceUnit
from personal_finance.domain.models import RUB, AccountKind
from personal_finance.presentation.investment_portfolio import (
    InvestmentPortfolioDialog,
    InvestmentPortfolioOptions,
)
from personal_finance.presentation.investment_preview import InvestmentPreviewDialog
from personal_finance.presentation.main_window import MainWindow
from tests.conftest import PASSWORD
from tests.gui.test_planning_scenarios_workflow import current
from tests.integration.test_investment_position_upgrade import candidate_schema as candidate_schema
from tests.unit.test_investment_positions import inputs
from tests.unit.test_investments import TODAY


def test_saved_portfolio_uses_book_snapshot_and_opens_fifo_details(
    candidate_schema, tmp_path, qtbot
):
    session, _ = create_vault(tmp_path / "portfolio.pfvault", "Учебный портфель", PASSWORD)
    window = MainWindow()
    qtbot.addWidget(window)
    window.show()
    try:
        account = session.catalog.add_account("Счёт с ценой", AccountKind.INVESTMENT)
        other = session.catalog.add_account("Счёт без цены", AccountKind.INVESTMENT)
        known = inputs(account)
        price = ManualPrice(known.security.id, TODAY, PriceUnit.CASH_PER_SECURITY, Decimal(5), RUB)
        session.investment_positions.save(replace(known, prices=(price,)))
        session.investment_positions.save(replace(inputs(other), prices=()))
        before = session.ledger.operations(), session.ledger.balances()
        window.activate_session(session)
        window.navigation.setCurrentRow(2)
        window.investment_portfolio_action.trigger()
        options = current(window, InvestmentPortfolioOptions)
        options.as_of.setDate(QDate(TODAY.year, TODAY.month, TODAY.day))
        options.calculate.click()
        qtbot.waitUntil(lambda: not window._busy, timeout=15000)
        report = current(window, InvestmentPortfolioDialog)
        assert report.positions.model().rowCount() == 2
        assert report.totals.model().item(0, 3).text() == "1"
        assert report.totals.model().item(0, 6).text() == "—"
        assert not report.details.isEnabled()
        row_index = next(
            i
            for i, row in enumerate(report.report.rows)
            if row.record.inputs.account_id == account.id
        )
        report.positions.selectRow(row_index)
        report.details.click()
        details = current(window, InvestmentPreviewDialog)
        assert details.valuation.positions[0].price == price
        assert details.lots.model().rowCount() == 1
        window._report_forms.back.click()
        assert current(window, InvestmentPortfolioDialog) is report
        assert report.positions.selectionModel().selectedRows()[0].row() == row_index
        assert (session.ledger.operations(), session.ledger.balances()) == before
    finally:
        window.lock_book()


def test_portfolio_unavailable_in_book_without_position_storage(session, qtbot):
    window = MainWindow()
    qtbot.addWidget(window)
    window.activate_session(session)
    try:
        assert not window.investment_portfolio_action.isEnabled()
    finally:
        window.lock_book()
