from datetime import date
from decimal import Decimal

from PySide6.QtCore import QDate

from personal_finance.application.vault import create_vault, open_vault
from personal_finance.domain.investments import TradeKind
from personal_finance.domain.models import AccountKind
from personal_finance.presentation.investment_editors import (
    InvestmentEventEditor,
    ManualPriceEditor,
)
from personal_finance.presentation.investment_preview import (
    InvestmentPreviewDialog,
    InvestmentPreviewOptions,
)
from personal_finance.presentation.investment_price_history import ManualPriceHistory
from personal_finance.presentation.investment_storage import SavedInvestmentPositionsDialog
from personal_finance.presentation.main_window import MainWindow
from tests.conftest import PASSWORD
from tests.gui.test_investment_preview_gui import add, show_position
from tests.gui.test_planning_scenarios_workflow import current


def set_price(window, options, day, value):
    options.set_price.click()
    editor = current(window, ManualPriceEditor)
    editor.day.setDate(QDate(2026, 11, day))
    editor.value.setText(value)
    editor.apply.click()
    assert current(window, InvestmentPreviewOptions) is options


def test_save_open_and_revise_investment_position_in_embedded_workflow(tmp_path, qtbot):
    path = tmp_path / "positions.pfvault"
    session, _ = create_vault(path, "Синтетическая книга 0017", PASSWORD)
    session.catalog.add_account("Учебный брокерский счёт", AccountKind.INVESTMENT)
    before = (session.ledger.operations(), session.ledger.balances())
    window = MainWindow()
    options = show_position(window, session, qtbot)
    try:
        assert options.open_saved.isVisible() and not options.save_position.isEnabled()
        add(window, options, TradeKind.BUY, "30,00", quantity=3)
        set_price(window, options, 12, "12,00")
        set_price(window, options, 13, "13,50")
        options.save_position.click()
        qtbot.waitUntil(lambda: not window._busy, timeout=15000)
        (first,) = session.investment_positions.items()
        assert len(first.inputs.prices) == 2 and not options.account.isEnabled()
        options.open_saved.click()
        qtbot.waitUntil(lambda: not window._busy, timeout=15000)
        picker = current(window, SavedInvestmentPositionsDialog)
        picker.table.selectRow(0)
        picker.open_button.click()
        loaded = current(window, InvestmentPreviewOptions)
        assert loaded is not options and len(loaded.prices) == 2
        loaded.table.selectRow(0)
        loaded.edit.click()
        editor = current(window, InvestmentEventEditor)
        editor.quantity.setValue(4)
        editor.apply.click()
        loaded.save_position.click()
        qtbot.waitUntil(lambda: not window._busy, timeout=15000)
        (second,) = session.investment_positions.items()
        assert second.id == first.id and second.revision_id != first.revision_id
        assert second.inputs.events[0].quantity == 4 and second.inputs.prices == first.inputs.prices
        assert (session.ledger.operations(), session.ledger.balances()) == before
        window.lock_book()
        reopened = open_vault(path, PASSWORD)
        try:
            assert reopened.investment_positions.history(first.id) == [first, second]
            reopened.check_integrity()
        finally:
            reopened.close()
    finally:
        window.lock_book()


def test_manual_prices_edit_by_date_and_do_not_overwrite_existing_quote(session, qtbot):
    account = session.catalog.add_account("Учебный брокерский счёт", AccountKind.INVESTMENT)
    window = MainWindow()
    options = show_position(window, session, qtbot)
    options.account.setCurrentIndex(options.account.findData(account.id))
    nov_12, nov_13, nov_14 = (date(2026, 11, day) for day in (12, 13, 14))
    try:
        add(window, options, TradeKind.BUY, "30,00", quantity=3)
        set_price(window, options, 12, "12,00")
        set_price(window, options, 13, "13,50")
        options.save_position.click()
        qtbot.waitUntil(lambda: not window._busy, timeout=15000)
        (first,) = session.investment_positions.items()

        options.set_price.click()
        duplicate = current(window, ManualPriceEditor)
        duplicate.value.setText("99,00")
        duplicate.apply.click()
        assert current(window, ManualPriceEditor) is duplicate and duplicate.feedback.isVisible()
        assert options.prices[nov_12].value == Decimal("12.00")
        window._report_forms.back.click()

        options.price_history.click()
        history = current(window, ManualPriceHistory)
        assert [history.table.model().item(i, 0).text() for i in range(2)] == [
            "13.11.2026",
            "12.11.2026",
        ]
        history.table.selectRow(1)
        history.edit.click()
        editor = current(window, ManualPriceEditor)
        editor.day.setDate(QDate(nov_13))
        editor.apply.click()
        assert current(window, ManualPriceEditor) is editor and editor.feedback.isVisible()
        editor.day.setDate(QDate(nov_14))
        editor.value.setText("14,00")
        editor.apply.click()
        assert current(window, ManualPriceHistory) is history
        assert set(options.prices) == {nov_13, nov_14}
        history.table.selectRow(1)
        history.remove.click()
        assert set(options.prices) == {nov_14}
        assert session.investment_positions.get(first.id) == first

        window._report_forms.back.click()
        options.calculate.click()
        qtbot.waitUntil(lambda: not window._busy, timeout=15000)
        assert current(window, InvestmentPreviewDialog).valuation.positions[0].market_minor is None
        window._report_forms.back.click()
        options.as_of.setDate(QDate(nov_14))
        options.calculate.click()
        qtbot.waitUntil(lambda: not window._busy, timeout=15000)
        assert current(window, InvestmentPreviewDialog).valuation.positions[0].market_minor == 4200
        window._report_forms.back.click()
        options.save_position.click()
        qtbot.waitUntil(lambda: not window._busy, timeout=15000)
        old, new = session.investment_positions.history(first.id)
        assert old == first and [price.quoted_on for price in new.inputs.prices] == [nov_14]
    finally:
        window.lock_book()
