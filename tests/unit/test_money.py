import pytest
from hypothesis import given
from hypothesis import strategies as st

from personal_finance.domain.errors import ValidationError
from personal_finance.domain.models import MAX_MINOR, Currency, Money


@pytest.mark.parametrize(
    ("text", "minor"),
    [
        ("0", 0),
        ("0,01", 1),
        ("1 234,56", 123456),
        ("-42.10", -4210),
        ("1.000000000", 100),
        ("92233720368547758.07", MAX_MINOR),
    ],
)
def test_exact_input(text: str, minor: int) -> None:
    assert Money.parse(text).minor == minor


@pytest.mark.parametrize(
    "text",
    [
        "0.001",
        "NaN",
        "Infinity",
        "-Infinity",
        "",
        "рубль",
        "1e1000000",
        "1e-1000000",
        "1.000000000000000000000000000000000000000000000000000001",
        "92233720368547758.08",
    ],
)
def test_no_rounding_or_nonfinite_values(text: str) -> None:
    with pytest.raises(ValidationError):
        Money.parse(text)


@given(st.integers(min_value=-MAX_MINOR, max_value=MAX_MINOR))
def test_roundtrip_every_representable_minor_amount(minor: int) -> None:
    sign = "-" if minor < 0 else ""
    number = abs(minor)
    assert Money.parse(f"{sign}{number // 100}.{number % 100:02}").minor == minor


def test_currency_and_overflow_are_not_silent() -> None:
    with pytest.raises(ValidationError):
        Money(1) + Money(1, Currency("USD", 2))
    with pytest.raises(ValidationError):
        Money(MAX_MINOR) + Money(1)
    with pytest.raises(ValidationError):
        Money(True)
