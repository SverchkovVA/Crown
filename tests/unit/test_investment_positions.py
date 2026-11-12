from dataclasses import replace
from datetime import timedelta
from decimal import Decimal

import pytest

from personal_finance.domain.errors import ValidationError
from personal_finance.domain.investment_positions import (
    InvestmentPositionInputs,
    investment_position_from_json,
    investment_position_json,
)
from personal_finance.domain.investment_prices import ManualPrice, PriceUnit
from personal_finance.domain.investments import TradeKind
from personal_finance.domain.models import RUB, Money
from tests.unit.test_investments import ACCOUNT, BOND, OTHER, SHARE, TODAY, trade


def inputs(account=ACCOUNT):
    first = trade(1, TradeKind.BUY, 3, 1000, 1, account=account)
    second = trade(2, TradeKind.SELL, 1, 500, 5, account=account)
    later = ManualPrice(SHARE.id, TODAY, PriceUnit.CASH_PER_SECURITY, Decimal("5.5500"), RUB)
    prior = replace(later, quoted_on=TODAY - timedelta(days=1), value=Decimal("3.000"))
    return InvestmentPositionInputs(account.id, SHARE, (second, first), (later, prior))


def test_position_payload_is_canonical_and_preserves_exact_input_sources():
    position = inputs()
    raw = investment_position_json(position)
    parsed = investment_position_from_json(raw)
    assert parsed == position
    assert investment_position_json(parsed) == raw
    assert '"value":"5.55"' in raw and '"value":"3"' in raw
    assert '"amount_minor":1000' in raw
    report = parsed.check_for(ACCOUNT)
    assert report.positions[0].quantity == 2
    assert report.disposals[0].cost_minor == 334


def test_bond_zero_accrued_interest_and_percent_remain_explicit():
    entry = trade(1, TradeKind.BUY, 1, 97000, security=BOND, accrued=0)
    price = ManualPrice(
        BOND.id,
        TODAY,
        PriceUnit.PERCENT_OF_FACE,
        Decimal("97.00000001"),
        RUB,
        Money(100000),
        Money(0),
    )
    payload = InvestmentPositionInputs(ACCOUNT.id, BOND, (entry,), (price,))
    restored = investment_position_from_json(investment_position_json(payload))
    assert restored == payload
    assert restored.events[0].accrued_interest == Money(0)
    assert restored.prices[0].value == Decimal("97.00000001")


@pytest.mark.parametrize(
    "mutation",
    [
        lambda raw: " " + raw,
        lambda raw: raw.replace('"format":1', '"format":true'),
        lambda raw: raw.replace('"quantity":3', '"quantity":3.0'),
        lambda raw: raw.replace('"value":"5.55"', '"value":5.55'),
        lambda raw: raw.replace('"value":"5.55"', '"value":"5.5500"'),
        lambda raw: raw.replace('"prices":', '"extra":0,"prices":'),
        lambda raw: raw.replace('"on":"2026-11-12"', '"on":"20261112"'),
    ],
)
def test_noncanonical_payload_is_rejected(mutation):
    raw = investment_position_json(inputs())
    changed = mutation(raw)
    assert changed != raw
    with pytest.raises(ValidationError, match="формат"):
        investment_position_from_json(changed)


def test_position_scope_and_future_oversale_validation():
    position = inputs()
    with pytest.raises(ValidationError, match="другому"):
        position.check_for(OTHER)
    with pytest.raises(ValidationError, match="одному счёту"):
        replace(position, account_id=OTHER.id)
    with pytest.raises(ValidationError, match="одну дату"):
        replace(position, prices=(position.prices[0], position.prices[0]))
    first, sale = position.events
    invalid = replace(
        position, events=(first, replace(sale, quantity=4, occurred_on=TODAY + timedelta(days=1)))
    )
    with pytest.raises(ValidationError, match="недостаточно"):
        invalid.check_for(ACCOUNT)
