from dataclasses import replace
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from zoneinfo import ZoneInfo

import pytest

from personal_finance.application.market_price_refresh import (
    MarketQuote,
    QuoteBinding,
    QuoteInstrument,
    QuoteStatus,
    refresh_market_prices,
)
from personal_finance.domain.errors import ValidationError
from personal_finance.domain.investment_prices import PriceUnit, market_value_minor
from personal_finance.domain.models import RUB, Currency, Money
from tests.unit.test_investments import BOND, SHARE, identifier

TODAY = date(2026, 11, 15)
NOW = datetime(2026, 11, 15, 13, tzinfo=UTC)
STOCK_CODE = QuoteInstrument("example", "SYNTHETIC-SHARE", "STOCKS")
BOND_CODE = QuoteInstrument("example", "SYNTHETIC-BOND", "BONDS")
STOCK = QuoteBinding(SHARE, STOCK_CODE)
DEBT = QuoteBinding(BOND, BOND_CODE)


def stock(**values):
    quote = MarketQuote(STOCK_CODE, TODAY, PriceUnit.CASH_PER_SECURITY, Decimal("12.34"), RUB, NOW)
    return replace(quote, **values)


def bond(**values):
    quote = MarketQuote(
        BOND_CODE,
        TODAY,
        PriceUnit.PERCENT_OF_FACE,
        Decimal("99.2"),
        RUB,
        unpaid_face=Money(75000),
        accrued_per_security=Money(0),
    )
    return replace(quote, **values)


def refresh(provider, bindings, previous=None):
    return refresh_market_prices(
        provider, bindings, {} if previous is None else previous, as_of=TODAY, fetched_at=NOW
    )


def test_refresh_sends_only_unique_provider_instruments_and_adapts_to_each_local_security():
    clone = replace(SHARE, id=identifier(900), name="Другая учебная позиция")
    calls = []

    def source(request):
        calls.append(request)
        return (stock(), bond())

    assert refresh(source, ()) == () and calls == []
    rows = refresh(source, (STOCK, QuoteBinding(clone, STOCK_CODE), DEBT))
    assert calls == [(STOCK_CODE, BOND_CODE)]
    assert [row.status for row in rows] == [QuoteStatus.UPDATED] * 3
    assert [row.current.price_for(row.binding.security).security_id for row in rows] == [
        SHARE.id,
        clone.id,
        BOND.id,
    ]
    assert market_value_minor(BOND, rows[2].current.price_for(BOND), 3) == 223200


def test_mixed_providers_and_repeated_local_ids_fail_before_calling_the_source():
    def no_query(_):
        raise AssertionError("Запрос со смешанной областью не должен попасть к источнику.")

    another_provider = QuoteBinding(BOND, replace(BOND_CODE, provider="separate"))
    with pytest.raises(ValidationError, match="одному источнику"):
        refresh(no_query, (STOCK, another_provider))
    with pytest.raises(ValidationError, match="каждого инструмента"):
        refresh(no_query, (STOCK, STOCK))


def test_incompatible_bond_and_foreign_board_keep_existing_prices():
    old_bond = bond(quoted_on=TODAY - timedelta(days=1))
    old_stock = stock(quoted_on=TODAY - timedelta(days=1), quoted_at=None)
    original = {SHARE.id: old_stock, BOND.id: old_bond}
    wrong_board = stock(instrument=replace(STOCK_CODE, board="OTHER"))
    rates = (bond(unpaid_face=None), wrong_board)
    shares, debt = refresh(lambda _: rates, (STOCK, DEBT), original)
    assert (shares.status, shares.current) == (QuoteStatus.MISSING, old_stock)
    assert (debt.status, debt.current) == (QuoteStatus.INCOMPATIBLE, old_bond)
    assert original == {SHARE.id: old_stock, BOND.id: old_bond}


def test_single_failure_preserves_returned_quote_and_keeps_other_original():
    old = bond(quoted_on=TODAY - timedelta(days=1))

    def interrupted(_):
        yield stock()
        raise TimeoutError("Искусственный сетевой отказ")

    first, second = refresh(interrupted, (STOCK, DEBT), {BOND.id: old})
    assert (first.status, first.current) == (QuoteStatus.UPDATED, stock())
    assert (second.status, second.current) == (QuoteStatus.SOURCE_UNAVAILABLE, old)


@pytest.mark.parametrize(
    ("candidate", "expected"),
    [
        (stock(quoted_at=NOW - timedelta(minutes=1)), QuoteStatus.UPDATED),
        (stock(quoted_at=NOW - timedelta(minutes=3)), QuoteStatus.OLDER),
        (stock(quoted_at=NOW - timedelta(minutes=2)), QuoteStatus.CURRENT),
        (stock(quoted_at=None), QuoteStatus.AMBIGUOUS),
        (
            stock(quoted_at=NOW - timedelta(minutes=2), value=Decimal("12.35")),
            QuoteStatus.AMBIGUOUS,
        ),
        (stock(quoted_on=TODAY - timedelta(days=1), quoted_at=None), QuoteStatus.OLDER),
        (stock(quoted_on=TODAY + timedelta(days=1)), QuoteStatus.INCOMPATIBLE),
        (stock(quoted_at=NOW + timedelta(seconds=1)), QuoteStatus.INCOMPATIBLE),
        (stock(quoted_at=NOW.replace(tzinfo=None)), QuoteStatus.INCOMPATIBLE),
        (stock(currency=Currency("USD", 2)), QuoteStatus.INCOMPATIBLE),
        (stock(value=Decimal("NaN")), QuoteStatus.INCOMPATIBLE),
        (stock(value=Decimal(0)), QuoteStatus.INCOMPATIBLE),
    ],
)
def test_price_validation_and_monotonic_replacement(candidate, expected):
    prior = stock(quoted_at=NOW - timedelta(minutes=2))
    (row,) = refresh(lambda _: (candidate,), (STOCK,), {SHARE.id: prior})
    assert row.status is expected
    assert row.current == (candidate if expected is QuoteStatus.UPDATED else prior)


def test_conflicting_and_identical_rows_have_distinct_results():
    (conflict,) = refresh(lambda _: (stock(), stock(value=Decimal("12.35"))), (STOCK,))
    assert conflict.status is QuoteStatus.AMBIGUOUS and conflict.current is None
    (same,) = refresh(lambda _: (stock(), stock()), (STOCK,))
    assert same.status is QuoteStatus.UPDATED and same.current == stock()
    (invalid,) = refresh(lambda _: (stock(value=Decimal(1)), stock(value=1.0)), (STOCK,))
    assert invalid.status is QuoteStatus.INCOMPATIBLE and invalid.current is None


def test_changed_market_binding_cannot_fall_back_to_the_previous_venue():
    newer = QuoteBinding(SHARE, replace(STOCK_CODE, board="NEW"))
    (row,) = refresh(lambda _: (), (newer,), {SHARE.id: stock()})
    assert row.status is QuoteStatus.MISSING and row.current is None


def test_local_clock_repetition_compares_quotes_by_actual_utc_moment():
    zone = ZoneInfo("America/New_York")
    first_hour = datetime(2026, 11, 1, 1, 30, tzinfo=zone, fold=0)
    repeated_hour = first_hour.replace(fold=1)
    old = stock(quoted_on=first_hour.date(), quoted_at=first_hour)
    new = stock(quoted_on=repeated_hour.date(), quoted_at=repeated_hour, value=Decimal("12.35"))
    (row,) = refresh(lambda _: (new,), (STOCK,), {SHARE.id: old})
    assert row.status is QuoteStatus.UPDATED and row.current == new
    assert new.quoted_at - old.quoted_at == timedelta(hours=1)
