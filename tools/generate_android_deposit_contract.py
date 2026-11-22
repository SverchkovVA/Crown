"""Создаёт независимый рациональный эталон для Windows и Android; только синтетика."""

from calendar import isleap
from datetime import date, timedelta
from fractions import Fraction
from itertools import product
from pathlib import Path

DENOMINATOR = 100 * 10**8 * 365 * 366


def rounded(value: Fraction, mode: str) -> int:
    whole, remainder = divmod(value.numerator, value.denominator)
    twice = remainder * 2
    if mode == "down":
        return whole
    if twice < value.denominator:
        return whole
    if twice > value.denominator:
        return whole + 1
    return whole + (1 if mode == "half_up" else whole % 2)


def rows():
    start = date(2023, 12, 30)
    end = date(2024, 1, 4)
    policies = product(
        ("opening_included", "maturity_included"),
        ("before_movements", "after_movements"),
        ("fixed_365", "calendar_year"),
        ("half_up", "half_even", "down"),
        ("daily", "payout"),
        (False, True),
    )
    for boundary, moment, count, rounding, stage, capitalize in policies:
        balance = 100001
        waiting = Fraction(0)
        for offset in range(6):
            day = start + timedelta(days=offset)
            before = balance
            movement = {date(2023, 12, 31): 50003, date(2024, 1, 2): -24907}.get(day, 0)
            balance += movement
            base = before if moment == "before_movements" else balance
            rate = Fraction("7.12345678") if day < date(2024, 1, 2) else Fraction("12.5")
            divisor = 366 if count == "calendar_year" and isleap(day.year) else 365
            included = day != (end if boundary == "opening_included" else start)
            exact = Fraction(base) * rate / (100 * divisor) if included else Fraction(0)
            waiting += Fraction(rounded(exact, rounding)) if stage == "daily" else exact
            payout = rounded(waiting, rounding) if day in (date(2024, 1, 1), end) else 0
            capitalized = payout if capitalize else 0
            balance += capitalized
            if day in (date(2024, 1, 1), end):
                waiting = Fraction(0)
            numerator = exact * DENOMINATOR
            assert numerator.denominator == 1
            yield [
                boundary,
                moment,
                count,
                rounding,
                stage,
                int(capitalize),
                offset,
                base,
                movement,
                int(numerator),
                payout,
                capitalized,
                balance,
            ]


if __name__ == "__main__":
    target = Path(__file__).resolve().parents[1] / "shared/android-fixtures/deposits-v1.tsv"
    target.write_text(
        "\n".join("\t".join(map(str, row)) for row in rows()) + "\n", encoding="utf-8"
    )
