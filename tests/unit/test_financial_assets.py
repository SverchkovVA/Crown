from dataclasses import replace

from personal_finance.domain.financial_assets import summarize_financial_assets
from personal_finance.domain.investment_portfolio import report_saved_portfolio
from personal_finance.domain.investments import TradeKind
from personal_finance.domain.ledger import AccountBalance
from personal_finance.domain.models import AccountKind, Currency, Money
from tests.unit.test_investment_portfolio import quote, saved
from tests.unit.test_investments import ACCOUNT, OTHER, SHARE, TODAY, identifier, trade


def test_assets_separate_broker_cash_and_securities_without_adding_trade_cash_flow():
    bank = replace(OTHER, kind=AccountKind.DEBIT)
    cash = [AccountBalance(bank.id, Money(4000)), AccountBalance(ACCOUNT.id, Money(-100))]
    record = saved(2000, ACCOUNT, SHARE, [trade(1, TradeKind.BUY, 2, 700)], [quote(SHARE, "4")])
    report = report_saved_portfolio([bank, ACCOUNT], [record], TODAY)
    (row,) = summarize_financial_assets([bank, ACCOUNT], cash, report)

    assert (row.liquid_minor, row.investment_cash_minor, row.securities_minor) == (4000, -100, 800)
    assert row.total_minor == 4700


def test_missing_quotes_affect_only_their_currency_and_zero_price_is_valid():
    foreign = replace(OTHER, kind=AccountKind.CASH, currency="USD")
    currency = Currency("USD", 2)
    cash = [
        AccountBalance(foreign.id, Money(800, currency)),
        AccountBalance(ACCOUNT.id, Money(100)),
    ]
    event = trade(1, TradeKind.BUY, 1, 700)
    report = report_saved_portfolio(
        [ACCOUNT, foreign], [saved(2000, ACCOUNT, SHARE, [event])], TODAY
    )
    rub, usd = summarize_financial_assets([ACCOUNT, foreign], cash, report)

    assert rub.unpriced_count == 1 and rub.securities_minor is None and rub.total_minor is None
    assert usd.unpriced_count == 0 and usd.total_minor == 800

    record = saved(2000, ACCOUNT, SHARE, [event], [quote(SHARE, "0")])
    report = report_saved_portfolio([ACCOUNT], [record], TODAY)
    (rub,) = summarize_financial_assets([ACCOUNT], cash[1:], report)
    assert rub.unpriced_count == 0 and rub.securities_minor == 0 and rub.total_minor == 100


def test_unavailable_position_prevents_a_complete_asset_total():
    unavailable = replace(OTHER, id=identifier(300))
    record = saved(2000, unavailable, SHARE, [trade(1, TradeKind.BUY, 1, 700, account=unavailable)])
    report = report_saved_portfolio([ACCOUNT], [record], TODAY)
    (row,) = summarize_financial_assets([ACCOUNT], [AccountBalance(ACCOUNT.id, Money(100))], report)

    assert report.unavailable_count == 1 and row.total_minor is None
