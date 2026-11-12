from PySide6.QtCore import QDate

from personal_finance.domain.investments import DistributionKind, SecurityKind, TradeKind
from personal_finance.domain.models import AccountKind
from personal_finance.presentation.investment_editors import (
    InvestmentEventEditor,
    ManualPriceEditor,
)
from personal_finance.presentation.investment_preview import (
    InvestmentPreviewDialog,
    InvestmentPreviewOptions,
)
from personal_finance.presentation.main_window import MainWindow
from tests.gui.test_analytics_gui import fingerprints
from tests.gui.test_planning_scenarios_workflow import current


def show_position(window, session, qtbot):
    qtbot.addWidget(window)
    window.show()
    window.activate_session(session)
    window.navigation.setCurrentRow(2)
    window.investment_preview_action.trigger()
    options = current(window, InvestmentPreviewOptions)
    options.code.setText("учебная")
    options.as_of.setDate(QDate(2026, 11, 12))
    return options


def add(window, options, kind, amount, *, quantity=1, fee="0", accrued=None):
    options.add.click()
    editor = current(window, InvestmentEventEditor)
    editor.kind.setCurrentIndex(editor.kind.findData(kind.value))
    editor.quantity.setValue(quantity)
    editor.amount.setText(amount)
    editor.fee.setText(fee)
    if accrued is not None:
        editor.accrued.setText(accrued)
    editor.apply.click()
    assert current(window, InvestmentPreviewOptions) is options


def test_investment_preview_calculates_fifo_and_keeps_draft_after_back(session, qtbot):
    session.catalog.add_account("Учебный брокерский счёт", AccountKind.INVESTMENT)
    before = fingerprints(session)
    window = MainWindow()
    options = show_position(window, session, qtbot)
    try:
        add(window, options, TradeKind.BUY, "10,00", quantity=3, fee="0,01")
        add(window, options, TradeKind.BUY, "8,00", quantity=2, fee="0,02")
        add(window, options, TradeKind.SELL, "16,00", quantity=4, fee="0,10")
        add(window, options, DistributionKind.DIVIDEND, "0,42")
        options.set_price.click()
        editor = current(window, ManualPriceEditor)
        editor.value.setText("5,55")
        editor.apply.click()
        assert current(window, InvestmentPreviewOptions) is options
        options.calculate.click()
        qtbot.waitUntil(lambda: not window._busy, timeout=15000)
        result = current(window, InvestmentPreviewDialog)
        (position,) = result.valuation.positions
        assert position.position.quantity == 1
        assert position.position.cost_minor == 401 and position.position.realized_minor == 188
        assert position.position.distributions_minor == 42
        assert position.market_minor == 555 and position.unrealized_minor == 154
        assert result.allocations.model().rowCount() == 2
        assert result.distributions.model().rowCount() == 1
        window._report_forms.back.click()
        assert current(window, InvestmentPreviewOptions) is options
        options.table.selectRow(2)
        options.edit.click()
        edit = current(window, InvestmentEventEditor)
        assert edit.day.date() == QDate(2026, 11, 12) and edit.amount.text() == "16,00"
        edit.quantity.setValue(6)
        edit.apply.click()
        options.calculate.click()
        qtbot.waitUntil(lambda: not window._busy, timeout=15000)
        assert current(window, InvestmentPreviewOptions) is options
        assert "недостаточно" in window._report_forms.notice.text()
        assert fingerprints(session) == before
    finally:
        window._report_forms.clear()
        window.session = None


def test_bond_preview_uses_percent_of_face_and_excludes_future_price(session, qtbot):
    session.catalog.add_account("Учебный брокерский счёт", AccountKind.INVESTMENT)
    before = fingerprints(session)
    window = MainWindow()
    options = show_position(window, session, qtbot)
    try:
        options.kind.setCurrentIndex(options.kind.findData(SecurityKind.BOND.value))
        add(window, options, TradeKind.BUY, "1940,00", quantity=2, fee="0,40", accrued="3,30")
        options.set_price.click()
        editor = current(window, ManualPriceEditor)
        assert editor.nominal.isVisible() and editor.accrued.isVisible()
        editor.day.setDate(QDate(2026, 11, 13))
        editor.value.setText("97,005")
        editor.nominal.setText("1000")
        editor.accrued.setText("12,34")
        editor.apply.click()
        options.calculate.click()
        qtbot.waitUntil(lambda: not window._busy, timeout=15000)
        result = current(window, InvestmentPreviewDialog)
        assert result.valuation.positions[0].market_minor is None
        window._report_forms.back.click()
        options.as_of.setDate(QDate(2026, 11, 13))
        options.calculate.click()
        qtbot.waitUntil(lambda: not window._busy, timeout=15000)
        result = current(window, InvestmentPreviewDialog)
        row = result.valuation.positions[0]
        assert row.market_minor == 196478
        assert row.position.cost_minor == 194370
        assert row.price.quoted_on.isoformat() == "2026-11-13"
        assert fingerprints(session) == before
    finally:
        window._report_forms.clear()
        window.session = None
