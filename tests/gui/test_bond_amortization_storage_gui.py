from contextlib import closing

from PySide6.QtCore import QDate

from personal_finance.application.vault import create_vault, open_vault
from personal_finance.domain.investments import BondAmortization, SecurityKind, TradeKind
from personal_finance.domain.ledger import OperationDraft, OperationKind
from personal_finance.domain.models import AccountKind, CategoryKind, Money
from personal_finance.presentation.investment_cash_reconciliation import InvestmentCashDialog
from personal_finance.presentation.investment_editors import (
    AMORTIZATION_KIND,
    InvestmentEventEditor,
)
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
from tests.gui.test_planning_scenarios_workflow import current
from tests.unit.test_bond_amortization import NEXT


def test_amortization_is_saved_reopened_and_compared_with_cash(tmp_path, qtbot):
    path = tmp_path / "trial.pfvault"
    session, _ = create_vault(path, "Учебная книга 0018", PASSWORD)
    account = session.catalog.add_account("Учебный счёт", AccountKind.INVESTMENT)
    category = session.catalog.add_category("Учебное поступление", CategoryKind.INCOME)
    movement = session.ledger.add(
        OperationDraft(OperationKind.INCOME, NEXT, account.id, Money(75000), category.id)
    )
    original_cash = session.ledger.operations(), session.ledger.balances()
    window = MainWindow()
    options = show_position(window, session, qtbot)
    try:
        options.kind.setCurrentIndex(options.kind.findData(SecurityKind.BOND.value))
        add(window, options, TradeKind.BUY, "2700,00", quantity=3, fee="0,09", accrued="90,00")
        options.save_position.click()
        qtbot.waitUntil(lambda: not window._busy, timeout=15000)
        (first,) = session.investment_positions.items()

        options.as_of.setDate(QDate(NEXT))
        options.add.click()
        editor = current(window, InvestmentEventEditor)
        editor.kind.setCurrentIndex(editor.kind.findData(AMORTIZATION_KIND))
        editor.quantity.setValue(3)
        editor.face_before.setText("1000,00")
        editor.face_after.setText("750,00")
        editor.apply.click()
        assert options.save_position.isEnabled() and options.amortization_notice.isHidden()
        options.save_position.click()
        qtbot.waitUntil(lambda: not window._busy, timeout=15000)
        (latest,) = session.investment_positions.items()
        assert latest.id == first.id and latest.revision_id != first.revision_id

        options.open_saved.click()
        qtbot.waitUntil(lambda: not window._busy, timeout=15000)
        picker = current(window, SavedInvestmentPositionsDialog)
        picker.table.selectRow(0)
        picker.history_button.click()
        qtbot.waitUntil(lambda: not window._busy, timeout=15000)
        versions = current(window, InvestmentVersionHistoryDialog)
        assert versions.versions == (latest, first)
        versions.as_of.setDate(QDate(NEXT))
        versions.table.selectRow(0)
        versions.calculate.click()
        qtbot.waitUntil(lambda: not window._busy, timeout=15000)
        details = current(window, InvestmentPreviewDialog)
        (receipt,) = details.valuation.portfolio.amortizations
        assert receipt.event == latest.inputs.amortizations[0] and receipt.realized_minor == 7498

        window.investment_portfolio_action.trigger()
        select_date = current(window, InvestmentPortfolioOptions)
        select_date.as_of.setDate(QDate(NEXT))
        select_date.calculate.click()
        qtbot.waitUntil(lambda: not window._busy, timeout=15000)
        portfolio = current(window, InvestmentPortfolioDialog)
        portfolio.check_cash.click()
        cash = current(window, InvestmentCashDialog)
        index = next(
            i for i, row in enumerate(cash.rows) if isinstance(row.event, BondAmortization)
        )
        assert cash.table.model().item(index, 3).text() == "Частичное погашение"
        assert cash.table.model().item(index, 5).text() == "Один кандидат"
        (candidate,) = cash.rows[index].candidates
        assert candidate.operation.revision_id == movement.revision_id
        assert (session.ledger.operations(), session.ledger.balances()) == original_cash

        window.lock_book()
        with closing(open_vault(path, PASSWORD)) as reopened:
            assert reopened.investment_positions.history(latest.id) == [first, latest]
            reopened.check_integrity()
    finally:
        window.lock_book()
