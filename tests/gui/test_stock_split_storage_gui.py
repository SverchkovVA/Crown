from contextlib import closing
from datetime import date

from PySide6.QtCore import QDate

from personal_finance.application.vault import create_vault, open_vault
from personal_finance.domain.investments import TradeKind
from personal_finance.domain.models import AccountKind
from personal_finance.presentation.investment_editors import SPLIT_KIND, InvestmentEventEditor
from personal_finance.presentation.investment_portfolio import (
    InvestmentPortfolioDialog,
    InvestmentPortfolioOptions,
)
from personal_finance.presentation.investment_preview import InvestmentPreviewDialog
from personal_finance.presentation.investment_storage import (
    InvestmentVersionHistoryDialog,
    SavedInvestmentPositionsDialog,
)
from personal_finance.presentation.main_window import MainWindow
from tests.conftest import PASSWORD
from tests.gui.test_investment_preview_gui import add, show_position
from tests.gui.test_investment_storage_gui import set_price
from tests.gui.test_planning_scenarios_workflow import current
from tests.integration.test_stock_split_lifecycle import split_schema as split_schema


def test_split_saved_and_reopened_from_investment_forms(tmp_path, qtbot, split_schema):
    path = tmp_path / "split.pfvault"
    session, _ = create_vault(path, "Учебная книга 0018", PASSWORD)
    session.catalog.add_account("Учебный брокерский счёт", AccountKind.INVESTMENT)
    before = session.ledger.operations(), session.ledger.balances()
    window = MainWindow()
    options = show_position(window, session, qtbot)
    try:
        add(window, options, TradeKind.BUY, "30,00", quantity=3)
        set_price(window, options, 12, "12,00")
        options.save_position.click()
        qtbot.waitUntil(lambda: not window._busy, timeout=15000)
        (first,) = session.investment_positions.items()

        options.as_of.setDate(QDate(2026, 11, 13))
        options.add.click()
        editor = current(window, InvestmentEventEditor)
        editor.kind.setCurrentIndex(editor.kind.findData(SPLIT_KIND))
        editor.multiplier.setValue(100)
        editor.apply.click()
        assert options.save_position.isEnabled() and options.split_notice.isHidden()
        options.save_position.click()
        qtbot.waitUntil(lambda: not window._busy, timeout=15000)
        (second,) = session.investment_positions.items()
        assert second.id == first.id and second.revision_id != first.revision_id
        assert second.inputs.splits[0].multiplier == 100
        assert second.inputs.events == first.inputs.events

        options.open_saved.click()
        qtbot.waitUntil(lambda: not window._busy, timeout=15000)
        picker = current(window, SavedInvestmentPositionsDialog)
        assert picker.table.model().item(0, 2).text() == "2"
        picker.table.selectRow(0)
        picker.history_button.click()
        qtbot.waitUntil(lambda: not window._busy, timeout=15000)
        history = current(window, InvestmentVersionHistoryDialog)
        assert history.versions == (second, first)
        assert [history.table.model().item(row, 4).text() for row in range(2)] == ["2", "1"]
        history.as_of.setDate(QDate(2026, 11, 13))
        history.table.selectRow(0)
        history.calculate.click()
        qtbot.waitUntil(lambda: not window._busy, timeout=15000)
        result = current(window, InvestmentPreviewDialog)
        (row,) = result.valuation.positions
        assert (row.position.quantity, row.position.cost_minor, row.price) == (300, 3000, None)
        assert (session.ledger.operations(), session.ledger.balances()) == before

        window.investment_portfolio_action.trigger()
        portfolio = current(window, InvestmentPortfolioOptions)
        portfolio.as_of.setDate(QDate(date(2026, 11, 13)))
        portfolio.calculate.click()
        qtbot.waitUntil(lambda: not window._busy, timeout=15000)
        report = current(window, InvestmentPortfolioDialog)
        report.positions.selectRow(0)
        report.details.click()
        details = current(window, InvestmentPreviewDialog)
        assert details.valuation.portfolio.splits == second.inputs.splits
        assert details.valuation.positions == result.valuation.positions

        window.lock_book()
        with closing(open_vault(path, PASSWORD)) as reopened:
            assert reopened.investment_positions.history(first.id) == [first, second]
    finally:
        window.lock_book()
