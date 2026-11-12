from datetime import date

import pytest

from personal_finance.domain.analytics import ReportPeriod
from personal_finance.domain.balance_forecast import (
    ForecastDay,
    PaymentMatch,
    check_reserve,
    forecast_balance,
    forecast_balances,
    match_payments,
)
from personal_finance.domain.errors import ValidationError
from personal_finance.domain.ledger import Operation, OperationDraft, OperationKind
from personal_finance.domain.models import MAX_MINOR, Account, AccountKind, Currency, Money
from personal_finance.domain.recurring_payments import PaymentDirection, PaymentSchedule, RepeatUnit

ACCOUNT = Account("00000000-0000-4000-8000-000000000001", "Проверочный", AccountKind.CASH, None)
OTHER = "00000000-0000-4000-8000-000000000002"
CATEGORY = "00000000-0000-4000-8000-000000000003"
BEFORE = date(2026, 1, 30)
START = date(2026, 1, 31)
FEBRUARY = date(2026, 2, 28)
END = date(2026, 3, 31)


def record(number, draft, *, deleted=False):
    return Operation(
        f"00000000-0000-4000-8000-{number:012d}",
        f"00000000-0000-4000-8001-{number:012d}",
        draft,
        deleted,
    )


def test_balance_forecast_uses_accepted_asset_postings_and_keeps_plans_separate():
    transfer = record(
        12,
        OperationDraft(
            OperationKind.TRANSFER,
            START,
            ACCOUNT.id,
            Money(3000),
            destination_account_id=OTHER,
            fee=Money(100),
            fee_category_id=CATEGORY,
        ),
    )
    income = record(
        13, OperationDraft(OperationKind.INCOME, START, ACCOUNT.id, Money(2000), CATEGORY)
    )
    operations = (
        transfer,
        income,
        record(11, OperationDraft(OperationKind.OPENING, BEFORE, ACCOUNT.id, Money(10000))),
        record(14, OperationDraft(OperationKind.OPENING, BEFORE, OTHER, Money(90000))),
        record(
            15,
            OperationDraft(OperationKind.EXPENSE, START, ACCOUNT.id, Money(5000), CATEGORY),
            deleted=True,
        ),
        record(
            16,
            OperationDraft(
                OperationKind.EXPENSE, date(2026, 4, 1), ACCOUNT.id, Money(5000), CATEGORY
            ),
        ),
    )
    plans = (
        PaymentSchedule("Подписка", Money(400), PaymentDirection.EXPENSE, START, RepeatUnit.MONTHS),
        PaymentSchedule("Проценты", Money(900), PaymentDirection.INCOME, FEBRUARY),
    )
    result = forecast_balance(operations, ACCOUNT, ReportPeriod(START, END), plans)
    assert result.opening_minor == 10000
    assert result.days == (
        ForecastDay(START, -1100, -400, 8500),
        ForecastDay(FEBRUARY, 0, 500, 9000),
        ForecastDay(END, 0, -400, 8600),
    )
    assert result.closing_minor == 8600
    assert tuple(v.operation for v in result.booked) == (transfer, income)
    assert [v.occurs_on for v in result.planned] == [START, FEBRUARY, FEBRUARY, END]


def test_combined_forecast_counts_only_fee_of_internal_transfer_and_one_plan():
    second = Account(OTHER, "Второй счёт", AccountKind.CASH, None)
    operations = (
        record(11, OperationDraft(OperationKind.OPENING, BEFORE, ACCOUNT.id, Money(10000))),
        record(12, OperationDraft(OperationKind.OPENING, BEFORE, OTHER, Money(5000))),
        record(
            13,
            OperationDraft(
                OperationKind.TRANSFER,
                START,
                ACCOUNT.id,
                Money(2000),
                destination_account_id=OTHER,
                fee=Money(50),
                fee_category_id=CATEGORY,
            ),
        ),
    )
    plan = PaymentSchedule("Покупка", Money(100), PaymentDirection.EXPENSE, START)
    period = ReportPeriod(START, START)
    result = forecast_balances(operations, (ACCOUNT, second), period, (plan,))
    assert result.accounts == (ACCOUNT, second) and result.account is None
    assert result.opening_minor == 15000
    assert result.days == (ForecastDay(START, -50, -100, 14850),)
    assert {move.account_id: move.minor for move in result.booked} == {
        ACCOUNT.id: -2050,
        second.id: 2000,
    }
    with pytest.raises(ValidationError, match="одного счёта"):
        match_payments(result, (PaymentMatch(0, operations[-1].revision_id),))
    with pytest.raises(ValidationError, match="один раз"):
        forecast_balances(operations, (ACCOUNT, ACCOUNT), period, ())
    with pytest.raises(ValidationError, match="валюты"):
        forecast_balances(
            operations, (ACCOUNT, Account(OTHER, "Иена", AccountKind.CASH, None, "JPY")), period, ()
        )


def test_balance_forecast_counts_first_day_opening_and_keeps_empty_period_constant():
    opening = record(11, OperationDraft(OperationKind.OPENING, START, ACCOUNT.id, Money(-500)))
    same = forecast_balance((opening,), ACCOUNT, ReportPeriod(START, START), ())
    assert same.opening_minor == 0 and same.closing_minor == -500
    assert same.days == (ForecastDay(START, -500, 0, -500),)
    later = forecast_balance((opening,), ACCOUNT, ReportPeriod(FEBRUARY, END), ())
    assert later.opening_minor == later.closing_minor == -500
    assert not later.days and not later.booked and not later.planned


def test_balance_forecast_keeps_exact_totals_above_single_operation_limit():
    plan = PaymentSchedule(
        "Проверочное пополнение", Money(MAX_MINOR), PaymentDirection.INCOME, START
    )
    result = forecast_balance((), ACCOUNT, ReportPeriod(START, START), (plan, plan))
    assert result.closing_minor == 2 * MAX_MINOR


def test_balance_forecast_rejects_currency_mismatch():
    plan = PaymentSchedule(
        "В другой валюте", Money(10, Currency("JPY", 0)), PaymentDirection.INCOME, START
    )
    with pytest.raises(ValidationError, match="валют"):
        forecast_balance((), ACCOUNT, ReportPeriod(START, END), (plan,))
    with pytest.raises(ValidationError, match="валют"):
        forecast_balance((), ACCOUNT, ReportPeriod(START, END), (), currency=Currency("JPY", 0))


def test_payment_match_keeps_one_booked_movement_and_can_be_undone():
    operation = record(
        12, OperationDraft(OperationKind.EXPENSE, START, ACCOUNT.id, Money(1500), CATEGORY)
    )
    plan = PaymentSchedule("Подписка", Money(1500), PaymentDirection.EXPENSE, START)
    forecast = forecast_balance((operation,), ACCOUNT, ReportPeriod(START, END), (plan, plan))
    assert forecast.closing_minor == -4500
    link = PaymentMatch(0, operation.revision_id)
    changed = match_payments(forecast, (link,))
    assert changed.matches == (link,)
    assert changed.days == (ForecastDay(START, -1500, -1500, -3000),)
    assert changed.booked == forecast.booked and changed.planned == forecast.planned
    assert match_payments(changed, ()) == forecast
    with pytest.raises(ValidationError, match="один раз"):
        match_payments(changed, (link, PaymentMatch(1, operation.revision_id)))


@pytest.mark.parametrize("minor, actual_on", ((1400, START), (1500, FEBRUARY)))
def test_payment_match_requires_same_date_amount_and_direction(minor, actual_on):
    operation = record(
        12, OperationDraft(OperationKind.EXPENSE, actual_on, ACCOUNT.id, Money(minor), CATEGORY)
    )
    plan = PaymentSchedule("Подписка", Money(1500), PaymentDirection.EXPENSE, START)
    forecast = forecast_balance((operation,), ACCOUNT, ReportPeriod(START, END), (plan,))
    with pytest.raises(ValidationError, match="датой и суммой"):
        match_payments(forecast, (PaymentMatch(0, operation.revision_id),))
    wrong_sign = PaymentSchedule(
        "Ожидаемый доход", Money(minor), PaymentDirection.INCOME, actual_on
    )
    forecast = forecast_balance((operation,), ACCOUNT, ReportPeriod(START, END), (wrong_sign,))
    with pytest.raises(ValidationError, match="датой и суммой"):
        match_payments(forecast, (PaymentMatch(0, operation.revision_id),))


@pytest.mark.parametrize(
    "plan_on, direction, amount, reserve, first_below, low_day, low_value",
    (
        (START, PaymentDirection.INCOME, 100, 100, None, START, 100),
        (FEBRUARY, PaymentDirection.INCOME, 100, 100, START, START, 0),
        (FEBRUARY, PaymentDirection.EXPENSE, 100, 0, FEBRUARY, FEBRUARY, -100),
        (END, PaymentDirection.EXPENSE, 0, 5, START, START, 0),
    ),
)
def test_reserve_uses_end_of_day_and_includes_days_without_movements(
    plan_on, direction, amount, reserve, first_below, low_day, low_value
):
    plans = (
        (PaymentSchedule("Учебное событие", Money(amount), direction, plan_on),) if amount else ()
    )
    forecast = forecast_balance((), ACCOUNT, ReportPeriod(START, END), plans)
    result = check_reserve(forecast, Money(reserve))
    assert (result.first_below, result.minimum_on, result.minimum_minor) == (
        first_below,
        low_day,
        low_value,
    )
    assert result.shortfall_minor == max(0, reserve - low_value)


def test_reserve_rejects_negative_threshold_or_another_currency():
    forecast = forecast_balance((), ACCOUNT, ReportPeriod(START, END), ())
    for value in (Money(-1), Money(0, Currency("JPY", 0))):
        with pytest.raises(ValidationError, match="резерв"):
            check_reserve(forecast, value)
