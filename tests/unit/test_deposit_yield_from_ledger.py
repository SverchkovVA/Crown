from datetime import timedelta
from decimal import Decimal

from personal_finance.domain.deposit_plan_fact import deposit_plan_fact
from personal_finance.domain.ledger import Operation, OperationDraft, OperationKind, OperationPart
from personal_finance.domain.models import Account, AccountKind, Money
from tests.unit.test_deposit_plan_fact import (
    ACCOUNT,
    INTEREST,
    OPENING,
    OTHER,
    comparison_scenario,
    income,
)
from tests.unit.test_deposits import uid

END = OPENING + timedelta(days=365)
WALLET = Account(uid(919), "Учебный кошелёк", AccountKind.DEBIT, None)


def entry(identifier, kind, day, amount, **options):
    account_id = options.pop("account_id", ACCOUNT.id)
    return Operation(
        uid(identifier),
        uid(identifier + 100),
        OperationDraft(kind, day, account_id, Money(amount), **options),
    )


def report(operations, *, dedicated=True):
    return deposit_plan_fact(
        comparison_scenario(), operations, ACCOUNT, INTEREST, END, dedicated_account=dedicated
    )


def test_compares_recorded_income_and_effective_return_without_counting_interest_twice():
    opening = entry(940, OperationKind.OPENING, OPENING, 100000)
    credited = income(941, END, 10000)
    payout = entry(942, OperationKind.TRANSFER, END, 110000, destination_account_id=WALLET.id)
    closed = report((payout, credited, opening))
    assert closed.actual_minor == 10000
    assert closed.actual_yield.closing_minor == 0
    assert abs(closed.actual_yield.annual_percent - Decimal(10)) < Decimal("1e-48")
    assert {r.operation.revision_id for r in closed.actual_yield.movements} == {
        opening.revision_id,
        credited.revision_id,
        payout.revision_id,
    }
    active = report((opening, credited))
    assert active.actual_yield.closing_minor == 110000
    assert active.actual_yield.annual_percent == closed.actual_yield.annual_percent
    assert report((opening, credited), dedicated=False).actual_yield is None


def test_funding_and_withdrawal_fees_are_borne_once_by_the_investor():
    to_deposit = entry(
        940,
        OperationKind.TRANSFER,
        OPENING,
        100000,
        account_id=WALLET.id,
        destination_account_id=ACCOUNT.id,
        fee=Money(2000),
        fee_category_id=uid(920),
    )
    credited = income(941, END, 2000)
    payout = entry(
        942,
        OperationKind.TRANSFER,
        END,
        101000,
        destination_account_id=WALLET.id,
        fee=Money(1000),
        fee_category_id=uid(920),
    )
    account = report((to_deposit, credited, payout))
    expected = (Decimal(101000) / Decimal(102000) - 1) * 100
    assert account.actual_yield.closing_minor == 0
    assert abs(account.actual_yield.annual_percent - expected) < Decimal("1e-24")


def test_incomplete_attribution_preserves_existing_interest_report():
    opening = entry(940, OperationKind.OPENING, OPENING, 100000)
    split = entry(
        941,
        OperationKind.INCOME,
        END,
        2000,
        parts=(OperationPart(INTEREST.id, Money(1000)), OperationPart(OTHER.id, Money(1000))),
    )
    result = report((opening, split))
    assert result.actual_minor == 1000 and result.actual_yield.annual_percent is None
    assert "вне пополнений" in result.actual_yield.unavailable_reason

    old = entry(943, OperationKind.OPENING, OPENING - timedelta(days=1), 10000)
    result = report((old, income(944, END, 1000)))
    assert result.actual_minor == 1000 and result.actual_yield.annual_percent is None
    assert "Перед датой открытия" in result.actual_yield.unavailable_reason
