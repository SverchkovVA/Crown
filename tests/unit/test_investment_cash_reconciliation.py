from dataclasses import replace
from datetime import timedelta

from personal_finance.domain.investment_cash_reconciliation import (
    InvestmentCashStatus,
    reconcile_investment_cash,
)
from personal_finance.domain.investment_portfolio import report_saved_portfolio
from personal_finance.domain.investments import TradeKind
from personal_finance.domain.ledger import Operation, OperationDraft, OperationKind
from personal_finance.domain.models import Money
from tests.unit.test_investment_portfolio import saved
from tests.unit.test_investments import ACCOUNT, OTHER, SHARE, TODAY, identifier, trade


def expense(number, amount, *, account=ACCOUNT, day=TODAY, deleted=False):
    return Operation(
        identifier(number),
        identifier(number + 10),
        OperationDraft(OperationKind.EXPENSE, day, account.id, Money(amount), identifier(1234)),
        deleted,
    )


def test_reconciliation_uses_exact_signed_cash_delta_and_excludes_unrelated_rows():
    event = trade(1, TradeKind.BUY, 2, 1000, 21)
    exact = expense(501, 1021)
    future = expense(502, 1021, day=TODAY + timedelta(days=1))
    deleted = expense(503, 1021, deleted=True)
    wrong_account = expense(504, 1021, account=OTHER)
    gross_only = expense(505, 1000)
    opposite = replace(
        exact, id=identifier(506), draft=replace(exact.draft, kind=OperationKind.INCOME)
    )
    opening = Operation(
        identifier(507),
        identifier(517),
        OperationDraft(OperationKind.OPENING, TODAY, ACCOUNT.id, Money(-1021)),
    )
    portfolio = report_saved_portfolio([ACCOUNT], [saved(2000, ACCOUNT, SHARE, [event])], TODAY)

    (row,) = reconcile_investment_cash(
        portfolio, [gross_only, exact, future, deleted, wrong_account, opposite, opening]
    )

    assert row.minor == -1021 and row.status is InvestmentCashStatus.UNIQUE
    (movement,) = row.candidates
    assert movement.operation is exact


def test_equal_events_or_multiple_entries_remain_ambiguous():
    first = trade(1, TradeKind.BUY, 2, 1000, 21)
    second = trade(2, TradeKind.BUY, 1, 1000, 21)
    expected = saved(2000, ACCOUNT, SHARE, [first, second])
    full = report_saved_portfolio([ACCOUNT], [expected], TODAY)
    one_movement = reconcile_investment_cash(full, [expense(501, 1021)])
    assert all(row.status is InvestmentCashStatus.AMBIGUOUS for row in one_movement)
    assert all(len(row.candidates) == 1 for row in one_movement)

    one_event = report_saved_portfolio([ACCOUNT], [saved(2000, ACCOUNT, SHARE, [first])], TODAY)
    (ambiguous,) = reconcile_investment_cash(one_event, [expense(501, 1021), expense(502, 1021)])
    assert ambiguous.status is InvestmentCashStatus.AMBIGUOUS and len(ambiguous.candidates) == 2


def test_absent_and_zero_movements_are_distinct():
    from tests.unit.test_investments import BOND

    purchase = trade(1, TradeKind.BUY, 1, 1000, security=BOND, accrued=0)
    redemption = trade(2, TradeKind.REDEEM, 1, 0, security=BOND, accrued=0)
    portfolio = report_saved_portfolio(
        [ACCOUNT], [saved(2000, ACCOUNT, BOND, [purchase, redemption])], TODAY
    )

    zero, missing = reconcile_investment_cash(portfolio, [])
    assert zero.status is InvestmentCashStatus.NO_MOVEMENT and not zero.candidates
    assert missing.status is InvestmentCashStatus.MISSING and not missing.candidates
