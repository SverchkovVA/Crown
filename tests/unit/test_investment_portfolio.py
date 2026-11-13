from dataclasses import replace
from datetime import timedelta
from decimal import Decimal

from personal_finance.domain.investment_portfolio import (
    portfolio_allocation,
    report_saved_portfolio,
)
from personal_finance.domain.investment_positions import (
    InvestmentPositionInputs,
    SavedInvestmentPosition,
)
from personal_finance.domain.investment_prices import ManualPrice, PriceUnit
from personal_finance.domain.investments import TradeKind
from personal_finance.domain.models import Currency
from tests.unit.test_investments import ACCOUNT, FUND, OTHER, SHARE, TODAY, identifier, trade


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


def test_allocation_uses_open_positions_and_separate_currency_denominators():
    jpy = Currency("JPY", 0)
    foreign_account = replace(OTHER, currency=jpy.code)
    foreign = replace(SHARE, currency=jpy)
    records = [
        saved(1000, ACCOUNT, SHARE, [trade(1, TradeKind.BUY, 2, 500)], [quote(SHARE, "5")]),
        saved(
            1001,
            ACCOUNT,
            FUND,
            [trade(1, TradeKind.BUY, 1, 550, security=FUND)],
            [quote(FUND, "20")],
        ),
        saved(
            1002,
            foreign_account,
            foreign,
            [trade(1, TradeKind.BUY, 3, 100, account=foreign_account, security=foreign)],
            [quote(foreign, "600")],
        ),
        saved(
            1003,
            ACCOUNT,
            SHARE,
            [trade(1, TradeKind.BUY, 1, 500), trade(2, TradeKind.SELL, 1, 600)],
        ),
    ]
    rows = portfolio_allocation(report_saved_portfolio([ACCOUNT, foreign_account], records, TODAY))

    assert [(row.currency.code, row.kind.value, row.open_count) for row in rows] == [
        ("JPY", "stock", 1),
        ("RUB", "fund", 1),
        ("RUB", "stock", 1),
    ]
    assert [row.priced_market_minor for row in rows] == [1800, 2000, 1000]
    assert [row.share_basis_points for row in rows] == [10000, 6667, 3333]


def test_allocation_hides_denomination_share_on_partial_prices_or_unavailable_accounts():
    records = [
        saved(1000, ACCOUNT, SHARE, [trade(1, TradeKind.BUY, 1, 500)], [quote(SHARE, "5")]),
        saved(1001, ACCOUNT, FUND, [trade(1, TradeKind.BUY, 1, 600, security=FUND)]),
        saved(1002, OTHER, SHARE, [trade(1, TradeKind.BUY, 1, 500, account=OTHER)]),
    ]
    fund, share = portfolio_allocation(report_saved_portfolio([ACCOUNT], records[:2], TODAY))
    assert (fund.priced_market_minor, fund.unpriced_count, fund.share_basis_points) == (
        None,
        1,
        None,
    )
    assert (share.priced_market_minor, share.unpriced_count, share.share_basis_points) == (
        500,
        0,
        None,
    )

    (partial,) = portfolio_allocation(
        report_saved_portfolio([ACCOUNT], [records[0], records[2]], TODAY)
    )
    assert (partial.priced_market_minor, partial.share_basis_points) == (500, None)


def test_allocation_distinguishes_zero_price_from_a_zero_total():
    zero = saved(1000, ACCOUNT, SHARE, [trade(1, TradeKind.BUY, 1, 500)], [quote(SHARE, "0")])
    fund = saved(
        1001, ACCOUNT, FUND, [trade(1, TradeKind.BUY, 1, 600, security=FUND)], [quote(FUND, "5")]
    )
    (no_denominator,) = portfolio_allocation(report_saved_portfolio([ACCOUNT], [zero], TODAY))
    assert (no_denominator.priced_market_minor, no_denominator.share_basis_points) == (0, None)
    valued, zero_share = portfolio_allocation(
        report_saved_portfolio([ACCOUNT], [zero, fund], TODAY)
    )
    assert (valued.share_basis_points, zero_share.share_basis_points) == (10000, 0)
