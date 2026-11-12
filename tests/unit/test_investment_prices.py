from dataclasses import replace
from datetime import timedelta
from decimal import Decimal, localcontext

import pytest

from personal_finance.domain.errors import ValidationError
from personal_finance.domain.investment_prices import (
    ManualPrice,
    PriceUnit,
    market_value_minor,
    value_investments,
)
from personal_finance.domain.investments import TradeKind, project_investments
from personal_finance.domain.models import RUB, Currency, Money
from tests.unit.test_investments import ACCOUNT, BOND, FUND, OTHER, SHARE, TODAY, trade


def manual(security, value, *, day=TODAY, currency=None):
    return ManualPrice(
        security.id,
        day,
        PriceUnit.CASH_PER_SECURITY,
        Decimal(value),
        security.currency if currency is None else currency,
    )


def test_stock_valuation_rounds_once_at_total_even_with_small_decimal_context():
    price = manual(SHARE, "0.005")
    with localcontext() as context:
        context.prec = 2
        assert market_value_minor(SHARE, price, 3) == 2
        assert market_value_minor(SHARE, price, 5) == 3
        assert market_value_minor(SHARE, price, 0) == 0


def test_bond_market_amount_has_unpaid_face_and_accrued_interest_per_security():
    price = ManualPrice(
        BOND.id,
        TODAY,
        PriceUnit.PERCENT_OF_FACE,
        Decimal("97.005"),
        RUB,
        Money(100000),
        Money(1234),
    )
    with localcontext() as context:
        context.prec = 2
        assert market_value_minor(BOND, price, 3) == 294717
    premium = replace(price, value=Decimal("101.23456"), unpaid_face=Money(50000))
    assert market_value_minor(BOND, premium, 2) == 103703


def test_most_recent_eligible_quote_marks_partial_total_and_preserves_sources():
    buy = trade(1, TradeKind.BUY, 2, 1000)
    fund = trade(2, TradeKind.BUY, 3, 900, security=FUND)
    report = project_investments((ACCOUNT,), (SHARE, FUND), (buy, fund), TODAY)
    prior = manual(SHARE, "7.50", day=TODAY - timedelta(days=1))
    today = manual(SHARE, "8.50")
    future = manual(SHARE, "900", day=TODAY + timedelta(days=1))
    snapshot = value_investments(report, (future, today, prior))
    share, missing = snapshot.positions
    assert share.price == today and share.market_minor == 1700 and share.unrealized_minor == 700
    assert (
        missing.price is None and missing.market_minor is None and missing.unrealized_minor is None
    )
    (total,) = snapshot.totals
    assert total.missing_count == 1
    assert (total.priced_market_minor, total.priced_cost_minor, total.total_cost_minor) == (
        1700,
        1000,
        1900,
    )
    assert total.priced_unrealized_minor == 700
    historic = value_investments(report, (future,))
    assert historic.totals[0].missing_count == 2


def test_currency_totals_stay_separate_and_explicit_zero_is_priced():
    yen = Currency("JPY", 0)
    foreign = replace(FUND, currency=yen)
    account = replace(OTHER, currency="JPY")
    buy = trade(1, TradeKind.BUY, 1, 100)
    buy_yen = trade(2, TradeKind.BUY, 5, 100, account=account, security=foreign)
    report = project_investments((ACCOUNT, account), (SHARE, foreign), (buy, buy_yen), TODAY)
    valuation = value_investments(report, (manual(SHARE, "0"), manual(foreign, "42.5")))
    jpy, rub = valuation.totals
    assert jpy.currency == yen and jpy.priced_market_minor == 213
    assert rub.currency == RUB and rub.priced_market_minor == 0 and rub.missing_count == 0


def test_closed_position_does_not_need_price():
    buy = trade(1, TradeKind.BUY, 1, 100)
    sale = trade(2, TradeKind.SELL, 1, 150)
    report = project_investments((ACCOUNT,), (SHARE,), (buy, sale), TODAY)
    snapshot = value_investments(report, ())
    assert snapshot.positions == snapshot.totals == ()
    assert snapshot.portfolio.disposals[0].realized_minor == 50


@pytest.mark.parametrize("value", ["NaN", "Infinity", "-1", "0.000000001", "1e5000", "1e19"])
def test_rejects_nonfinite_out_of_range_and_excess_precision(value):
    with pytest.raises(ValidationError, match="значение|Цена"):
        manual(SHARE, value)


def test_incompatible_or_duplicate_price_and_currency_definition_are_rejected():
    buy = trade(1, TradeKind.BUY, 1, 100)
    report = project_investments((ACCOUNT,), (SHARE,), (buy,), TODAY)
    first = manual(SHARE, "12")
    second = manual(SHARE, "13")
    with pytest.raises(ValidationError, match="несколько"):
        value_investments(report, (first, second))
    with pytest.raises(ValidationError, match="валюте"):
        value_investments(report, (manual(SHARE, "12", currency=Currency("JPY", 0)),))
    with pytest.raises(ValidationError, match="процент"):
        market_value_minor(BOND, manual(BOND, "12"), 1)
    with pytest.raises(ValidationError, match="номинал"):
        ManualPrice(BOND.id, TODAY, PriceUnit.PERCENT_OF_FACE, Decimal("95"), RUB)
    with pytest.raises(ValidationError, match="разной точностью"):
        project_investments(
            (ACCOUNT,), (SHARE, replace(FUND, currency=Currency("RUB", 3))), (buy,), TODAY
        )
