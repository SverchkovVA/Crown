from dataclasses import replace
from datetime import timedelta
from decimal import Decimal

from PySide6.QtWidgets import QLabel

from personal_finance.domain.investment_portfolio import report_saved_portfolio
from personal_finance.domain.investment_positions import SavedInvestmentPosition
from personal_finance.domain.investments import StockSplit
from personal_finance.domain.models import Money
from personal_finance.presentation.investment_portfolio import InvestmentPortfolioDialog
from personal_finance.presentation.investment_preview import (
    InvestmentPreviewDialog,
    InvestmentPreviewRequest,
)
from tests.integration.test_bond_amortization_storage import bond as bond_inputs
from tests.integration.test_bond_amortization_storage import with_payment
from tests.unit.test_investment_positions import inputs
from tests.unit.test_investments import ACCOUNT, identifier
from tests.unit.test_market_price_refresh import TODAY, bond, stock


def saved(position):
    return SavedInvestmentPosition(
        identifier(801), identifier(802), position, False, "2026-11-15T09:00:00Z"
    )


def calculate(record, observation, as_of=TODAY):
    return report_saved_portfolio(
        (ACCOUNT,), (record,), as_of, market_quotes={record.inputs.security.id: observation}
    )


def test_offline_quote_leaves_stored_inputs_intact_and_manual_wins_on_same_day():
    original = saved(inputs())
    observation = stock(value=Decimal("10.50"))
    report = calculate(original, observation)
    (row,) = report.rows
    assert row.record == original and row.market_minor == 2100
    assert row.valuation.positions[0].market_quote == observation
    assert report.totals[0].priced_market_minor == 2100
    manual = replace(original.inputs.prices[-1], quoted_on=TODAY)
    same_day = saved(replace(original.inputs, prices=(manual,)))
    (row,) = calculate(same_day, observation).rows
    assert row.market_minor == 1110 and row.valuation.positions[0].market_quote is None
    (past,) = calculate(original, observation, as_of=TODAY - timedelta(days=1)).rows
    assert past.market_minor == 1110 and past.valuation.positions[0].market_quote is None


def test_market_price_before_split_and_quote_with_wrong_remaining_face_are_ineligible():
    position = inputs()
    split = StockSplit(identifier(804), ACCOUNT.id, position.security.id, TODAY, 1, 3)
    original = saved(replace(position, splits=(split,)))
    (row,) = calculate(original, stock(quoted_on=TODAY - timedelta(days=1))).rows
    assert row.position.quantity == 6 and row.market_minor is None
    (same_day,) = calculate(original, stock(value=Decimal("10.50"))).rows
    assert same_day.market_minor == 6300

    amortized = saved(with_payment(bond_inputs()))
    (ineligible,) = calculate(amortized, bond(unpaid_face=Money(100000))).rows
    assert ineligible.market_minor is None
    (matching,) = calculate(amortized, bond()).rows
    assert (
        matching.market_minor == 223200 and matching.valuation.positions[0].market_quote == bond()
    )


def test_portfolio_and_position_show_readable_source_with_no_extra_column(qtbot):
    record = saved(inputs())
    report = calculate(record, stock(value=Decimal("10.50")))
    portfolio = InvestmentPortfolioDialog(report)
    qtbot.addWidget(portfolio)
    assert portfolio.positions.model().columnCount() == 6
    tip = portfolio.positions.model().item(0, 4).toolTip()
    assert "Источник example" in tip and "Режим STOCKS" in tip and "13:00 UTC" in tip
    (row,) = report.rows
    view = InvestmentPreviewDialog(
        InvestmentPreviewRequest(
            ACCOUNT, record.inputs.security, record.inputs.events, TODAY, record.inputs.prices
        ),
        row.valuation,
    )
    qtbot.addWidget(view)
    source = [
        label.text() for label in view.findChildren(QLabel) if "Источник example" in label.text()
    ]
    assert len(source) == 1 and "10,50 ₽ за бумагу" in source[0]
