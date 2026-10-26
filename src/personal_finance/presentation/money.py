"""Русское отображение точных денежных значений без float."""

from personal_finance.domain.models import Money


def format_money(value: Money, *, include_currency: bool = True) -> str:
    whole, fractional = divmod(abs(value.minor), 10**value.currency.exponent)
    result = ("-" if value.minor < 0 else "") + f"{whole:,}".replace(",", " ")
    if value.currency.exponent:
        result += "," + str(fractional).zfill(value.currency.exponent)
    return result + (" " + value.currency.code if include_currency else "")
