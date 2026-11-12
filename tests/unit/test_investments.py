from dataclasses import replace
from datetime import date, datetime, timedelta
from uuid import UUID

import pytest
from hypothesis import given
from hypothesis import strategies as st

from personal_finance.domain.errors import ValidationError
from personal_finance.domain.investments import (
    DistributionKind,
    FifoAllocation,
    Security,
    SecurityDistribution,
    SecurityKind,
    SecurityTrade,
    TradeKind,
    project_investments,
)
from personal_finance.domain.models import MAX_MINOR, Account, AccountKind, Currency, Money

TODAY = date(2026, 11, 12)


def identifier(number):
    return str(UUID(int=number))


ACCOUNT = Account(identifier(1), "Учебный счёт", AccountKind.INVESTMENT, None)
OTHER = Account(identifier(2), "Другой счёт", AccountKind.INVESTMENT, None)
SHARE = Security(identifier(3), "test", "Учебная акция", SecurityKind.STOCK)
BOND = Security(identifier(4), "bond", "Учебная облигация", SecurityKind.BOND)
FUND = Security(identifier(5), "fund", "Учебный фонд", SecurityKind.FUND)


def trade(
    seq,
    kind,
    quantity,
    amount,
    fee=0,
    *,
    account=ACCOUNT,
    security=SHARE,
    day=TODAY,
    accrued=None,
):
    return SecurityTrade(
        identifier(100 + seq),
        account.id,
        security.id,
        day,
        seq,
        kind,
        quantity,
        Money(amount, security.currency),
        Money(fee, security.currency),
        None if accrued is None else Money(accrued, security.currency),
    )


def test_fifo_matches_multiple_lots_and_returns_explainable_sources():
    first = trade(1, TradeKind.BUY, 3, 1000, 1)
    second = trade(2, TradeKind.BUY, 2, 800, 2)
    sale = trade(3, TradeKind.SELL, 4, 1600, 10)
    dividend = SecurityDistribution(
        identifier(200), ACCOUNT.id, SHARE.id, TODAY, 4, DistributionKind.DIVIDEND, Money(42)
    )
    snapshot = project_investments((ACCOUNT,), (SHARE,), (sale, dividend, second, first), TODAY)
    (position,) = snapshot.positions
    (disposal,) = snapshot.disposals
    assert SHARE.code == "TEST"
    assert disposal.trade == sale and sale.cash_delta_minor == 1590
    assert disposal.allocations == (
        FifoAllocation(first.id, 3, 1001),
        FifoAllocation(second.id, 1, 401),
    )
    assert disposal.realized_minor == position.realized_minor == 188
    assert position.quantity == 1 and position.cost_minor == 401
    assert position.lots[0].acquisition_id == second.id
    assert position.distributions_minor == 42 and snapshot.distributions == (dividend,)


def test_partial_sales_keep_every_minor_unit():
    buy = trade(1, TradeKind.BUY, 3, 1000, 1)
    sales = tuple(trade(seq, TradeKind.SELL, 1, 500) for seq in range(2, 5))
    snapshot = project_investments((ACCOUNT,), (SHARE,), (buy, *sales), TODAY)
    assert [row.cost_minor for row in snapshot.disposals] == [334, 333, 334]
    assert snapshot.positions[0].lots == ()
    assert snapshot.positions[0].realized_minor == 499


@given(
    total=st.integers(min_value=1, max_value=MAX_MINOR),
    partitions=st.lists(st.integers(min_value=1, max_value=1000), min_size=1, max_size=12),
)
def test_fifo_conserves_cost_for_any_partition(total, partitions):
    buy = trade(1, TradeKind.BUY, sum(partitions), total)
    sales = tuple(
        trade(seq, TradeKind.SELL, quantity, 1) for seq, quantity in enumerate(partitions, start=2)
    )
    snapshot = project_investments((ACCOUNT,), (SHARE,), (buy, *sales), TODAY)
    assert sum(disposal.cost_minor for disposal in snapshot.disposals) == total
    assert all(disposal.cost_minor >= 0 for disposal in snapshot.disposals)
    assert snapshot.positions[0].quantity == snapshot.positions[0].cost_minor == 0


def test_bond_coupon_redemption_and_accrued_interest_are_separate():
    purchase = trade(1, TradeKind.BUY, 2, 194000, 40, security=BOND, accrued=330)
    redeem = trade(2, TradeKind.REDEEM, 1, 100000, 10, security=BOND, accrued=0)
    coupon = SecurityDistribution(
        identifier(200), ACCOUNT.id, BOND.id, TODAY, 3, DistributionKind.COUPON, Money(2000)
    )
    snapshot = project_investments((ACCOUNT,), (BOND,), (purchase, redeem, coupon), TODAY)
    (position,) = snapshot.positions
    assert purchase.cash_delta_minor == -194370
    assert redeem.cash_delta_minor == 99990
    assert position.cost_minor == 97185 and position.quantity == 1
    assert position.realized_minor == 2805 and position.distributions_minor == 2000


def test_fund_distributions_can_arrive_after_sale():
    buy = trade(1, TradeKind.BUY, 2, 100, security=FUND)
    sale = trade(2, TradeKind.SELL, 2, 150, security=FUND)
    distribution = SecurityDistribution(
        identifier(200),
        ACCOUNT.id,
        FUND.id,
        TODAY + timedelta(days=1),
        1,
        DistributionKind.DIVIDEND,
        Money(16),
    )
    report = project_investments(
        (ACCOUNT,), (FUND,), (buy, sale, distribution), distribution.occurred_on
    )
    assert report.positions[0].quantity == 0
    assert report.positions[0].realized_minor == 50
    assert report.positions[0].distributions_minor == 16


def test_historical_cutoff_does_not_borrow_from_other_account_or_future_purchase():
    prior = trade(1, TradeKind.BUY, 3, 300, account=OTHER)
    sale = trade(2, TradeKind.SELL, 1, 100)
    future = trade(3, TradeKind.BUY, 1, 100, day=TODAY + timedelta(days=1))
    assert (
        project_investments((ACCOUNT, OTHER), (SHARE,), (future, prior), TODAY).positions[0].account
        == OTHER
    )
    assert (
        project_investments(
            (ACCOUNT, OTHER), (SHARE,), (future, prior, sale), TODAY - timedelta(days=1)
        ).positions
        == ()
    )
    with pytest.raises(ValidationError, match="недостаточно"):
        project_investments((ACCOUNT, OTHER), (SHARE,), (future, prior, sale), TODAY)


def test_same_day_order_is_explicit_and_repeated_orders_are_rejected():
    sale = trade(1, TradeKind.SELL, 1, 100)
    buy = trade(2, TradeKind.BUY, 2, 100)
    with pytest.raises(ValidationError, match="недостаточно"):
        project_investments((ACCOUNT,), (SHARE,), (buy, sale), TODAY)
    duplicate = replace(sale, id=identifier(201), sequence=buy.sequence)
    with pytest.raises(ValidationError, match="повторяется"):
        project_investments((ACCOUNT,), (SHARE,), (buy, duplicate), TODAY)
    duplicate_id = replace(buy, sequence=3)
    with pytest.raises(ValidationError, match="повторяется"):
        project_investments((ACCOUNT,), (SHARE,), (buy, duplicate_id), TODAY)


@pytest.mark.parametrize(
    ("changes", "message"),
    [
        ({"quantity": True}, "количество"),
        ({"quantity": 0}, "количество"),
        ({"sequence": 0}, "номер"),
        ({"occurred_on": datetime(2026, 11, 12)}, "дату"),
        ({"fee": Money(-1)}, "Комиссия"),
        ({"fee": Money(1, Currency("JPY", 0))}, "валюте"),
        ({"amount": Money(0)}, "сумму"),
        ({"amount": Money(MAX_MINOR), "fee": Money(1)}, "предел"),
    ],
)
def test_invalid_trade_inputs(changes, message):
    buy = trade(1, TradeKind.BUY, 1, 100)
    with pytest.raises(ValidationError, match=message):
        replace(buy, **changes)


@pytest.mark.parametrize(
    ("entry", "security", "message"),
    [
        (trade(1, TradeKind.BUY, 1, 100, security=BOND), BOND, "НКД"),
        (trade(1, TradeKind.BUY, 1, 100, accrued=0), SHARE, "НКД"),
        (trade(1, TradeKind.REDEEM, 1, 100), SHARE, "Погашение"),
        (
            SecurityDistribution(
                identifier(250), ACCOUNT.id, SHARE.id, TODAY, 1, DistributionKind.COUPON, Money(100)
            ),
            SHARE,
            "Купоны",
        ),
        (
            replace(
                trade(1, TradeKind.BUY, 1, 100),
                amount=Money(100, Currency("RUB", 3)),
                fee=Money(0, Currency("RUB", 3)),
            ),
            SHARE,
            "Валюты",
        ),
    ],
)
def test_event_security_contract(entry, security, message):
    with pytest.raises(ValidationError, match=message):
        project_investments((ACCOUNT,), (security,), (entry,), TODAY)


def test_invalid_or_unavailable_scope_is_rejected():
    buy = trade(1, TradeKind.BUY, 1, 100)
    with pytest.raises(ValidationError, match="инвестиционные"):
        project_investments((replace(ACCOUNT, kind=AccountKind.CASH),), (SHARE,), (buy,), TODAY)
    with pytest.raises(ValidationError, match="отсутствует"):
        project_investments((ACCOUNT,), (), (buy,), TODAY)
    with pytest.raises(ValidationError, match="счёт повторяется"):
        project_investments((ACCOUNT, ACCOUNT), (SHARE,), (buy,), TODAY)
    with pytest.raises(ValidationError, match="Инструмент повторяется"):
        project_investments((ACCOUNT,), (SHARE, SHARE), (buy,), TODAY)
