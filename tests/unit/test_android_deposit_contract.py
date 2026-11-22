"""Общий рациональный эталон расчёта вкладов для Windows и Android."""

from datetime import date
from decimal import Decimal
from pathlib import Path
from uuid import UUID

from personal_finance.domain.deposits import (
    AccrualBoundary,
    BalanceMoment,
    DayCount,
    DepositMovement,
    DepositRate,
    DepositScenario,
    InterestRounding,
    RoundingStage,
    forecast_deposit,
)
from personal_finance.domain.models import Money


def test_android_deposit_vectors_match_desktop_engine():
    path = Path(__file__).resolve().parents[2] / "shared/android-fixtures/deposits-v1.tsv"
    groups = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        row = line.split("\t")
        groups.setdefault(tuple(row[:6]), []).append(row)
    assert len(groups) == 96

    def uid(number):
        return str(UUID(int=number))

    for options, rows in groups.items():
        value = DepositScenario(
            opened_on=date(2023, 12, 30),
            matures_on=date(2024, 1, 4),
            initial=Money(100001),
            rates=(
                DepositRate(uid(1), date(2023, 12, 30), Decimal("7.12345678")),
                DepositRate(uid(2), date(2024, 1, 2), Decimal("12.5")),
            ),
            movements=(
                DepositMovement(uid(3), date(2023, 12, 31), Money(50003)),
                DepositMovement(uid(4), date(2024, 1, 2), Money(-25007)),
                DepositMovement(uid(5), date(2024, 1, 2), Money(100)),
            ),
            payout_dates=(date(2024, 1, 1), date(2024, 1, 4)),
            boundary=AccrualBoundary(options[0]),
            balance_moment=BalanceMoment(options[1]),
            day_count=DayCount(options[2]),
            rounding=InterestRounding(options[3]),
            rounding_stage=RoundingStage(options[4]),
            capitalize=options[5] == "1",
            allow_topups=True,
            allow_withdrawals=True,
            minimum=Money(0),
        )
        actual = forecast_deposit(value).days
        assert len(actual) == 6
        for row in rows:
            day = actual[int(row[6])]
            assert (
                day.base_minor,
                day.movement_minor,
                day.interest_numerator,
                day.payout_minor,
                day.capitalized_minor,
                day.closing_minor,
            ) == tuple(map(int, row[7:]))
