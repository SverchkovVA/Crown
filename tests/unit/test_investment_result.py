from personal_finance.domain.investment_prices import PositionResult, position_result
from personal_finance.domain.investments import (
    DistributionKind,
    SecurityDistribution,
    TradeKind,
    project_investments,
)
from personal_finance.domain.models import Money
from tests.unit.test_investments import ACCOUNT, SHARE, TODAY, identifier, trade


def test_open_position_has_total_from_realized_cash_and_market_value():
    first = trade(1, TradeKind.BUY, 3, 1000, 1)
    second = trade(2, TradeKind.BUY, 2, 800, 2)
    sale = trade(3, TradeKind.SELL, 4, 1600, 10)
    dividend = SecurityDistribution(
        identifier(999), ACCOUNT.id, SHARE.id, TODAY, 4, DistributionKind.DIVIDEND, Money(42)
    )
    (position,) = project_investments(
        (ACCOUNT,), (SHARE,), (first, second, sale, dividend), TODAY
    ).positions
    assert position.purchases_minor == 1803
    assert position_result(position, 555) == PositionResult(384, 2130)
    assert position_result(position, None) is None


def test_closed_position_has_total_without_quote_and_rounds_negative_ratio():
    buy = trade(1, TradeKind.BUY, 1, 800)
    sell = trade(2, TradeKind.SELL, 1, 799)
    (position,) = project_investments((ACCOUNT,), (SHARE,), (buy, sell), TODAY).positions
    assert position.quantity == 0
    assert position_result(position, None) == PositionResult(-1, -13)


def test_payout_without_purchase_has_amount_and_no_ratio():
    dividend = SecurityDistribution(
        identifier(999), ACCOUNT.id, SHARE.id, TODAY, 1, DistributionKind.DIVIDEND, Money(42)
    )
    (position,) = project_investments((ACCOUNT,), (SHARE,), (dividend,), TODAY).positions
    assert position_result(position, None) == PositionResult(42, None)
