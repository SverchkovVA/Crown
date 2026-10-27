"""Русское отображение точных денежных значений без float."""

from personal_finance.domain.models import Currency, Money


def format_money(value: Money, *, include_currency: bool = True) -> str:
    return format_minor(value.minor, value.currency, include_currency=include_currency)


def format_minor(minor: int, currency: Currency, *, include_currency: bool = True) -> str:
    """Поддерживает также точную разницу двух крайних остатков без записи в Money."""
    whole, fractional = divmod(abs(minor), 10**currency.exponent)
    result = ("-" if minor < 0 else "") + f"{whole:,}".replace(",", " ")
    if currency.exponent:
        result += "," + str(fractional).zfill(currency.exponent)
    return result + (" " + currency.code if include_currency else "")
