from dataclasses import replace
from datetime import timedelta
from decimal import Decimal

import pytest

from personal_finance.domain.errors import ValidationError
from personal_finance.domain.investment_prices import ManualPrice, PriceUnit, value_investments
from personal_finance.domain.investments import (
    BondAmortization,
    DistributionKind,
    PrincipalCostAllocation,
    SecurityDistribution,
    TradeKind,
    project_investments,
)
from personal_finance.domain.models import MAX_MINOR, RUB, Money
from tests.unit.test_investments import ACCOUNT, BOND, SHARE, TODAY, identifier, trade

NEXT = TODAY + timedelta(days=1)
LATER = TODAY + timedelta(days=2)


def principal(quantity=3, *, sequence=3, day=NEXT, before=100000, after=75000):
    return BondAmortization(
        identifier(200 + sequence),
        ACCOUNT.id,
        BOND.id,
        day,
        sequence,
        quantity,
        Money(before),
        Money(after),
    )


def test_partial_redemption_preserves_quantity_and_splits_principal_from_coupon():
    bought = trade(1, TradeKind.BUY, 3, 270000, 9, security=BOND, accrued=9000)
    event = principal()
    coupon = SecurityDistribution(
        identifier(205), ACCOUNT.id, BOND.id, NEXT, 4, DistributionKind.COUPON, Money(9100)
    )

    before = project_investments(
        (ACCOUNT,), (BOND,), (bought, coupon), TODAY, amortizations=(event,)
    )
    assert before.amortizations == () and before.positions[0].cost_minor == 279009
    after = project_investments((ACCOUNT,), (BOND,), (coupon, bought), NEXT, amortizations=(event,))
    (position,) = after.positions
    (receipt,) = after.amortizations
    assert receipt.event == event and event.principal_minor == 75000
    assert receipt.allocations == (PrincipalCostAllocation(bought.id, 67502),)
    assert (position.quantity, position.cost_minor, position.realized_minor) == (3, 211507, 7498)
    assert position.distributions_minor == 9100 and position.purchases_minor == 279009
    assert after.distributions == (coupon,) and after.disposals == ()


def test_partial_sales_repeated_amortization_and_final_redemption_conserve_minor_units():
    first = trade(1, TradeKind.BUY, 2, 2001, 1, security=BOND, accrued=3)
    second = trade(2, TradeKind.BUY, 2, 2408, 2, security=BOND, accrued=5)
    sale = trade(3, TradeKind.SELL, 1, 1100, 7, security=BOND, accrued=11)
    first_repayment = principal(sequence=4, day=TODAY, before=1200, after=800)
    second_repayment = principal(sequence=2, day=NEXT, before=800, after=400)
    redemption = trade(5, TradeKind.REDEEM, 3, 1200, security=BOND, day=LATER, accrued=0)
    after = project_investments(
        (ACCOUNT,),
        (BOND,),
        (redemption, sale, second, first),
        LATER,
        amortizations=(second_repayment, first_repayment),
    )
    (position,) = after.positions
    assert tuple(row.event for row in after.amortizations) == (first_repayment, second_repayment)
    assert (position.quantity, position.cost_minor) == (0, 0)
    assert position.realized_minor == (
        sale.cash_delta_minor
        + first_repayment.principal_minor
        + second_repayment.principal_minor
        + redemption.cash_delta_minor
        - position.purchases_minor
    )
    assert (
        sum(row.cost_minor for row in (*after.disposals, *after.amortizations))
        == position.purchases_minor
    )


def test_quotes_need_the_remaining_nominal_on_or_after_the_payment():
    bought = trade(1, TradeKind.BUY, 3, 270000, security=BOND, accrued=0)
    event = principal()
    old_quote = ManualPrice(
        BOND.id, TODAY, PriceUnit.PERCENT_OF_FACE, Decimal("99.2"), RUB, Money(100000), Money(200)
    )
    wrong_nominal = replace(old_quote, quoted_on=NEXT)
    new_quote = replace(wrong_nominal, unpaid_face=Money(75000), accrued_per_security=Money(0))

    before = project_investments((ACCOUNT,), (BOND,), (bought,), TODAY, amortizations=(event,))
    after = project_investments((ACCOUNT,), (BOND,), (bought,), NEXT, amortizations=(event,))
    assert value_investments(before, (old_quote,)).positions[0].market_minor == 298200
    assert value_investments(after, (old_quote,)).positions[0].price is None
    assert value_investments(after, (old_quote, wrong_nominal)).positions[0].price is None
    assert value_investments(after, (old_quote, new_quote)).positions[0].market_minor == 223200


@pytest.mark.parametrize("before,after", [(100, 100), (100, 0), (100, -1), (100, 101)])
def test_amortization_rejects_full_zero_or_negative_decrease(before, after):
    with pytest.raises(ValidationError, match="уменьшение"):
        principal(before=before, after=after)


def test_amortization_refuses_broken_sequence_and_quantity_or_nonbond():
    bought = trade(1, TradeKind.BUY, 3, 270000, security=BOND, accrued=0)
    with pytest.raises(ValidationError, match="совпадать с остатком"):
        project_investments((ACCOUNT,), (BOND,), (bought,), NEXT, amortizations=(principal(2),))
    following = principal(sequence=4, before=76000, after=50000)
    with pytest.raises(ValidationError, match="после предыдущего"):
        project_investments(
            (ACCOUNT,), (BOND,), (bought,), NEXT, amortizations=(principal(), following)
        )
    with pytest.raises(ValidationError, match="порядковый номер"):
        project_investments(
            (ACCOUNT,), (BOND,), (bought,), TODAY, amortizations=(principal(sequence=1, day=TODAY),)
        )
    with pytest.raises(ValidationError, match="для облигаций"):
        project_investments(
            (ACCOUNT,),
            (SHARE,),
            (),
            NEXT,
            amortizations=(replace(principal(), security_id=SHARE.id),),
        )
    with pytest.raises(ValidationError, match="допустимый предел"):
        principal(before=MAX_MINOR, after=1)
