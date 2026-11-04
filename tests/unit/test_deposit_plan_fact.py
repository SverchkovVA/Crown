from dataclasses import replace
from datetime import date
from decimal import Decimal

import pytest

from personal_finance.domain.deposit_plan_fact import DepositInterestMonth, deposit_plan_fact
from personal_finance.domain.deposits import DayCount, DepositRate
from personal_finance.domain.errors import ValidationError
from personal_finance.domain.ledger import Operation, OperationDraft, OperationKind, OperationPart
from personal_finance.domain.models import (
    Account,
    AccountKind,
    Category,
    CategoryKind,
    Currency,
    Money,
)
from tests.unit.test_deposits import scenario, uid

OPENING = date(2026, 1, 30)
FIRST = date(2026, 1, 31)
MATURITY = date(2026, 2, 1)
LATE = date(2026, 3, 1)
ACCOUNT = Account(uid(20), "Счёт процентов", AccountKind.SAVINGS, None)
INTEREST = Category(uid(21), "Проценты", CategoryKind.INCOME)
OTHER = Category(uid(22), "Другой доход", CategoryKind.INCOME, INTEREST.id)


def comparison_scenario():
    return scenario(
        opened_on=OPENING,
        matures_on=MATURITY,
        initial=Money(100000),
        rates=(DepositRate(uid(1), OPENING, Decimal("36.5")),),
        movements=(),
        payout_dates=(FIRST, MATURITY),
        day_count=DayCount.FIXED_365,
        capitalize=False,
    )


def income(identifier, day, amount, *, account=ACCOUNT, category=INTEREST):
    return Operation(
        uid(identifier),
        uid(identifier + 100),
        OperationDraft(OperationKind.INCOME, day, account.id, Money(amount), category.id),
    )


def test_plan_fact_uses_payout_dates_and_only_current_selected_income_parts():
    split = Operation(
        uid(30),
        uid(130),
        OperationDraft(
            OperationKind.INCOME,
            FIRST,
            ACCOUNT.id,
            Money(130),
            parts=(OperationPart(INTEREST.id, Money(80)), OperationPart(OTHER.id, Money(50))),
        ),
    )
    later = income(31, LATE, 130)
    different_account = replace(ACCOUNT, id=uid(23))
    operations = [
        later,
        income(32, FIRST, 1000, account=different_account),
        replace(income(33, MATURITY, 1000), is_deleted=True),
        income(34, FIRST, 1000, category=OTHER),
        income(35, date(2026, 1, 29), 1000),
        Operation(
            uid(36),
            uid(136),
            OperationDraft(
                OperationKind.TRANSFER,
                MATURITY,
                different_account.id,
                Money(1000),
                destination_account_id=ACCOUNT.id,
            ),
        ),
        split,
    ]
    due = deposit_plan_fact(comparison_scenario(), operations, ACCOUNT, INTEREST, MATURITY)
    assert due.months == (
        DepositInterestMonth(date(2026, 1, 1), 100, 80),
        DepositInterestMonth(date(2026, 2, 1), 100, 0),
    )
    assert (due.planned_minor, due.actual_minor, due.difference_minor) == (200, 80, -120)
    assert [(flow.operation, flow.totals.income_minor) for flow in due.sources] == [(split, 80)]

    delayed = deposit_plan_fact(comparison_scenario(), operations, ACCOUNT, INTEREST, LATE)
    assert delayed.months[-1] == DepositInterestMonth(date(2026, 3, 1), 0, 130)
    assert (delayed.planned_minor, delayed.actual_minor, delayed.difference_minor) == (200, 210, 10)
    assert [flow.operation.revision_id for flow in delayed.sources] == [
        split.revision_id,
        later.revision_id,
    ]


def test_plan_fact_has_no_planned_payment_before_first_payout():
    report = deposit_plan_fact(comparison_scenario(), [], ACCOUNT, INTEREST, OPENING)
    assert report.months == report.sources == ()
    assert report.planned_minor == report.actual_minor == report.difference_minor == 0


def test_plan_fact_rejects_bad_date_category_and_currency():
    value = comparison_scenario()
    with pytest.raises(ValidationError, match="начало"):
        deposit_plan_fact(value, [], ACCOUNT, INTEREST, date(2026, 1, 29))
    with pytest.raises(ValidationError, match="доходную"):
        deposit_plan_fact(value, [], ACCOUNT, replace(INTEREST, kind=CategoryKind.EXPENSE), FIRST)
    with pytest.raises(ValidationError, match="счёт в валюте"):
        deposit_plan_fact(value, [], replace(ACCOUNT, currency="USD"), INTEREST, FIRST)
    usd = Currency("USD", 2)
    wrong = Operation(
        uid(31),
        uid(131),
        OperationDraft(
            OperationKind.INCOME,
            FIRST,
            ACCOUNT.id,
            Money(50, usd),
            INTEREST.id,
            fee=Money(0, usd),
        ),
    )
    with pytest.raises(ValidationError, match="записанных процентов"):
        deposit_plan_fact(value, [wrong], ACCOUNT, INTEREST, FIRST)
