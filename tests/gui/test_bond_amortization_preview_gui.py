from PySide6.QtCore import QDate
from PySide6.QtWidgets import QLabel

from personal_finance.domain.investments import SecurityKind, TradeKind
from personal_finance.domain.models import AccountKind
from personal_finance.presentation.investment_editors import (
    AMORTIZATION_KIND,
    InvestmentEventEditor,
    ManualPriceEditor,
)
from personal_finance.presentation.investment_preview import (
    InvestmentPreviewDialog,
    InvestmentPreviewOptions,
)
from personal_finance.presentation.main_window import MainWindow
from tests.gui.test_investment_preview_gui import add, show_position
from tests.gui.test_planning_scenarios_workflow import current


def test_partial_redemption_preview_separates_nominal_and_does_not_save(session, qtbot):
    session.catalog.add_account("Учебный брокерский счёт", AccountKind.INVESTMENT)
    before = session.ledger.operations(), session.ledger.balances()
    window = MainWindow()
    options = show_position(window, session, qtbot)
    try:
        options.add.click()
        editor = current(window, InvestmentEventEditor)
        assert editor.kind.findData(AMORTIZATION_KIND) == -1
        window._report_forms.back.click()
        options.kind.setCurrentIndex(options.kind.findData(SecurityKind.BOND.value))
        add(window, options, TradeKind.BUY, "2 700,00", quantity=3, fee="0,09", accrued="90,00")
        assert options.save_position.isEnabled()
        options.set_price.click()
        price = current(window, ManualPriceEditor)
        price.value.setText("99,2")
        price.nominal.setText("1 000,00")
        price.accrued.setText("2,00")
        price.apply.click()

        options.as_of.setDate(QDate(2026, 11, 13))
        options.add.click()
        editor = current(window, InvestmentEventEditor)
        editor.kind.setCurrentIndex(editor.kind.findData(AMORTIZATION_KIND))
        assert editor.face_before.isVisible() and editor.amount.isHidden()
        assert editor.accrued.isHidden() and editor.quantity.isVisible()
        editor.quantity.setValue(3)
        editor.face_before.setText("1 000,00")
        editor.face_after.setText("0")
        editor.apply.click()
        assert editor.feedback.isVisible()
        editor.face_after.setText("750,00")
        editor.apply.click()
        assert not options.save_position.isEnabled() and options.amortization_notice.isVisible()
        assert options.table.model().item(1, 4).text() == "750,00"
        options.table.selectRow(1)
        options.edit.click()
        editor = current(window, InvestmentEventEditor)
        assert editor.face_after.text() == "750,00" and editor.quantity.value() == 3
        editor.apply.click()

        options.calculate.click()
        qtbot.waitUntil(lambda: not window._busy, timeout=15000)
        report = current(window, InvestmentPreviewDialog)
        (row,) = report.valuation.positions
        assert (row.position.quantity, row.position.cost_minor, row.price) == (3, 211507, None)
        assert [report.amortizations.model().item(0, i).text() for i in range(2, 6)] == [
            "750,00",
            "750,00",
            "675,02",
            "74,98",
        ]
        assert any(
            "с оставшимся номиналом" in label.text() for label in report.findChildren(QLabel)
        )

        window._report_forms.back.click()
        assert current(window, InvestmentPreviewOptions) is options
        options.table.selectRow(1)
        options.remove.click()
        assert options.save_position.isEnabled() and options.amortization_notice.isHidden()
        assert session.investment_positions.items() == []
        assert (session.ledger.operations(), session.ledger.balances()) == before
    finally:
        window.lock_book()
