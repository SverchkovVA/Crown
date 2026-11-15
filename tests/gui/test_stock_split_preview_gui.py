from datetime import date

from PySide6.QtCore import QDate
from PySide6.QtWidgets import QLabel, QTabWidget

from personal_finance.domain.investments import SecurityKind
from personal_finance.domain.models import AccountKind
from personal_finance.presentation.investment_editors import SPLIT_KIND, InvestmentEventEditor
from personal_finance.presentation.investment_preview import (
    InvestmentPreviewDialog,
    InvestmentPreviewOptions,
)
from personal_finance.presentation.main_window import MainWindow


def test_split_is_previewed_without_saving_or_reusing_an_obsolete_quote(session, qtbot):
    session.catalog.add_account("Учебный брокерский счёт", AccountKind.INVESTMENT)
    prior = session.investment_positions.items(), session.ledger.operations()
    window = MainWindow()
    qtbot.addWidget(window)
    window.activate_session(session)
    window.navigation.setCurrentRow(2)
    window.show()
    panel = window._report_forms

    def current(cls):
        dialog = panel.stack.currentWidget().widget()
        assert isinstance(dialog, cls)
        return dialog

    try:
        window.investment_preview_action.trigger()
        options = current(InvestmentPreviewOptions)
        options.code.setText("DEMO-SPLIT")
        options.as_of.setDate(QDate(2026, 10, 10))
        options.kind.setCurrentIndex(options.kind.findData(SecurityKind.BOND.value))
        options.add.click()
        editor = current(InvestmentEventEditor)
        assert editor.kind.findData(SPLIT_KIND) == -1
        editor.reject()
        options.kind.setCurrentIndex(options.kind.findData(SecurityKind.STOCK.value))
        options.add.click()
        editor = current(InvestmentEventEditor)
        editor.quantity.setValue(3)
        editor.amount.setText("12,00")
        editor.apply.click()
        assert options.save_position.isEnabled()
        options.set_price.click()
        editor = panel.stack.currentWidget().widget()
        editor.value.setText("4,00")
        editor.apply.click()

        options.as_of.setDate(QDate(2026, 10, 11))
        options.add.click()
        editor = current(InvestmentEventEditor)
        editor.kind.setCurrentIndex(editor.kind.findData(SPLIT_KIND))
        assert editor.multiplier.isVisible() and editor.amount.isHidden()
        editor.multiplier.setValue(100)
        editor.apply.click()
        assert options.table.model().item(1, 3).text() == "×100"
        assert not options.save_position.isEnabled() and options.split_notice.isVisible()

        options.calculate.click()
        qtbot.waitUntil(lambda: not window._busy)
        view = current(InvestmentPreviewDialog)
        valuation = view.valuation
        assert (valuation.portfolio.as_of, valuation.portfolio.splits[0].multiplier) == (
            date(2026, 10, 11),
            100,
        )
        assert valuation.portfolio.positions[0].quantity == 300
        assert valuation.portfolio.positions[0].cost_minor == 1200
        assert valuation.positions[0].price is None
        assert any("после дробления" in label.text() for label in view.findChildren(QLabel))
        tabs = view.findChild(QTabWidget)
        assert "Дробление акций" in [tabs.tabText(i) for i in range(tabs.count())]

        panel.back.click()
        assert current(InvestmentPreviewOptions) is options
        options.table.selectRow(1)
        options.remove.click()
        assert options.save_position.isEnabled() and options.split_notice.isHidden()
        assert (session.investment_positions.items(), session.ledger.operations()) == prior
    finally:
        window.lock_book()
