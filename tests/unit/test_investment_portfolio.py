from dataclasses import replace
from datetime import timedelta
from decimal import Decimal

from personal_finance.domain.investment_portfolio import report_saved_portfolio
from personal_finance.domain.investment_positions import (
    InvestmentPositionInputs,
    SavedInvestmentPosition,
)
from personal_finance.domain.investment_prices import ManualPrice, PriceUnit
from personal_finance.domain.investments import TradeKind
from personal_finance.domain.models import Currency
from tests.unit.test_investments import ACCOUNT, OTHER, SHARE, TODAY, identifier, trade


def saved(key, account, security, events, prices=(), *, deleted=False):
    return SavedInvestmentPosition(
        identifier(key),
        identifier(key + 10),
        InvestmentPositionInputs(account.id, security, tuple(events), tuple(prices)),
        deleted,
        "2026-11-12T00:00:00Z",
    )


def quote(security, value, day=TODAY):
    return ManualPrice(
        security.id, day, PriceUnit.CASH_PER_SECURITY, Decimal(value), security.currency
    )


def test_portfolio_preserves_account_prices_and_exposes_partial_totals():
    first = saved(
        1000,
        ACCOUNT,
        SHARE,
        [trade(1, TradeKind.BUY, 2, 600)],
        [quote(SHARE, "4.02"), quote(SHARE, "9", TODAY + timedelta(days=1))],
    )
    second = saved(
        1001,
        OTHER,
        SHARE,
        [trade(1, TradeKind.BUY, 3, 660, account=OTHER)],
    )
    result = report_saved_portfolio([ACCOUNT, OTHER], [first, second], TODAY)

    assert {row.position.account.id: row.market_minor for row in result.rows} == {
        ACCOUNT.id: 804,
        OTHER.id: None,
    }
    (total,) = result.totals
    assert total.open_count == 2 and total.unpriced_count == 1
    assert total.cost_minor == 1260 and total.priced_market_minor == 804
    assert total.result_minor is None
    assert next(row for row in result.rows if row.record == first).price_date == TODAY


def test_closed_position_contributes_without_price_and_currencies_stay_separate():
    local = saved(
        1000,
        ACCOUNT,
        SHARE,
        [trade(1, TradeKind.BUY, 1, 500), trade(2, TradeKind.SELL, 1, 700, fee=10)],
    )
    currency = Currency("JPY", 0)
    foreign_account = replace(OTHER, currency=currency.code)
    foreign_security = replace(SHARE, currency=currency)
    foreign = saved(
        1001,
        foreign_account,
        foreign_security,
        [trade(1, TradeKind.BUY, 2, 900, account=foreign_account, security=foreign_security)],
        [quote(foreign_security, "460")],
    )
    result = report_saved_portfolio([ACCOUNT, foreign_account], [local, foreign], TODAY)

    jpy, rub = result.totals
    assert jpy.currency == currency and jpy.priced_market_minor == 920 and jpy.result_minor == 20
    assert rub.currency == SHARE.currency and rub.priced_market_minor == 0
    assert rub.open_count == 0 and rub.realized_minor == rub.result_minor == 190
    closed = next(row for row in result.rows if row.record == local)
    assert closed.position.quantity == 0 and closed.price_date is None


def test_archive_future_and_unavailable_positions_do_not_distort_totals():
    current = trade(1, TradeKind.BUY, 1, 600)
    future = replace(current, occurred_on=TODAY + timedelta(days=1))
    unavailable = trade(1, TradeKind.BUY, 1, 600, account=OTHER)
    result = report_saved_portfolio(
        [ACCOUNT],
        [
            saved(1000, ACCOUNT, SHARE, [current], deleted=True),
            saved(1001, ACCOUNT, SHARE, [future]),
            saved(1002, OTHER, SHARE, [unavailable]),
        ],
        TODAY,
    )

    assert not result.rows and not result.totals and result.unavailable_count == 1


def test_valid_zero_price_is_counted_but_all_missing_has_no_valuation():
    inputs = [trade(1, TradeKind.BUY, 1, 600)]
    no_quote = report_saved_portfolio([ACCOUNT], [saved(1000, ACCOUNT, SHARE, inputs)], TODAY)
    zero = report_saved_portfolio(
        [ACCOUNT], [saved(1000, ACCOUNT, SHARE, inputs, [quote(SHARE, "0")])], TODAY
    )

    assert no_quote.totals[0].priced_market_minor is None
    assert zero.totals[0].priced_market_minor == 0
    assert zero.totals[0].result_minor == -600
