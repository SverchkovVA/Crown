from datetime import timedelta
from decimal import Decimal

import pytest

from personal_finance.domain.errors import ValidationError
from personal_finance.domain.investment_positions import InvestmentPositionInputs
from personal_finance.domain.investment_prices import ManualPrice, PriceUnit, value_investments
from personal_finance.domain.investments import StockSplit, TradeKind, project_investments
from personal_finance.domain.models import MAX_MINOR, RUB
from tests.unit.test_investments import ACCOUNT, BOND, OTHER, SHARE, TODAY, identifier, trade


def split(factor=2, *, account=ACCOUNT, security=SHARE, day=TODAY, sequence=4):
    return StockSplit(identifier(1111), account.id, security.id, day, sequence, factor)


def test_stock_split_conserves_remaining_cost_and_fifo_after_partial_sales():
    first = trade(1, TradeKind.BUY, 3, 1000, fee=1)
    second = trade(2, TradeKind.BUY, 2, 1301)
    before = trade(3, TradeKind.SELL, 1, 450)
    after = trade(5, TradeKind.SELL, 5, 1400)
    event = split()
    snapshot = project_investments(
        (ACCOUNT,), (SHARE,), (after, before, first, second), TODAY, splits=(event,)
    )
    (position,) = snapshot.positions
    before_disposal, after_disposal = snapshot.disposals

    assert before_disposal.cost_minor == 334
    assert [
        (row.acquisition_id, row.quantity, row.cost_minor) for row in after_disposal.allocations
    ] == [(first.id, 4, 667), (second.id, 1, 325)]
    assert position.quantity == 3 and position.lots[0].acquisition_id == second.id
    assert position.cost_minor == 976
    assert position.purchases_minor == 2302
    assert position.realized_minor == (450 - 334) + (1400 - 992)
    assert position.cost_minor + sum(item.cost_minor for item in snapshot.disposals) == 2302
    assert snapshot.splits == (event,)
    with pytest.raises(ValidationError):
        InvestmentPositionInputs(ACCOUNT.id, SHARE, (first, event))


def test_stock_split_is_scoped_by_account_and_report_date_and_blocks_old_prices():
    yesterday = TODAY - timedelta(days=1)
    first = trade(1, TradeKind.BUY, 1, 1000, day=yesterday)
    second = trade(2, TradeKind.BUY, 1, 2000, account=OTHER, day=yesterday)
    event = split(100)
    old_price = ManualPrice(SHARE.id, yesterday, PriceUnit.CASH_PER_SECURITY, Decimal(10), RUB)
    new_price = ManualPrice(SHARE.id, TODAY, PriceUnit.CASH_PER_SECURITY, Decimal("0.10"), RUB)

    before = project_investments(
        (ACCOUNT, OTHER), (SHARE,), (first, second), yesterday, splits=(event,)
    )
    assert not before.splits
    assert [row.market_minor for row in value_investments(before, [old_price]).positions] == [
        1000,
        1000,
    ]

    after = project_investments((ACCOUNT, OTHER), (SHARE,), (first, second), TODAY, splits=(event,))
    affected, other = value_investments(after, [old_price]).positions
    assert affected.position.quantity == 100 and affected.position.cost_minor == 1000
    assert affected.market_minor is None and affected.price is None
    assert other.position.quantity == 1 and other.market_minor == 1000
    assert value_investments(after, [old_price, new_price]).positions[0].market_minor == 1000


def test_stock_split_rejects_absent_position_wrong_kind_and_duplicate_order():
    with pytest.raises(ValidationError, match="открытых акций"):
        project_investments((ACCOUNT,), (SHARE,), (), TODAY, splits=(split(),))
    with pytest.raises(ValidationError, match="для акций"):
        project_investments((ACCOUNT,), (BOND,), (), TODAY, splits=(split(security=BOND),))
    first = trade(1, TradeKind.BUY, 1, 100)
    with pytest.raises(ValidationError, match="повторяется"):
        project_investments((ACCOUNT,), (SHARE,), (first,), TODAY, splits=(split(sequence=1),))


def test_stock_split_rejects_overflow_and_non_integer_ratio():
    with pytest.raises(ValidationError, match="целым"):
        split(True)
    first = trade(1, TradeKind.BUY, MAX_MINOR, 100)
    with pytest.raises(ValidationError, match="допустимом"):
        project_investments((ACCOUNT,), (SHARE,), (first,), TODAY, splits=(split(),))
