"""Оценка портфеля по явно заданным датированным ценам."""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime
from decimal import Decimal
from enum import StrEnum

from personal_finance.domain.errors import ValidationError
from personal_finance.domain.investments import (
    BondAmortization,
    InvestmentPortfolio,
    Security,
    SecurityKind,
    SecurityPosition,
)
from personal_finance.domain.ledger import require_identifier
from personal_finance.domain.models import MAX_MINOR, Currency, Money


class PriceUnit(StrEnum):
    CASH_PER_SECURITY = "cash_per_security"
    PERCENT_OF_FACE = "percent_of_face"


@dataclass(frozen=True, slots=True)
class ManualPrice:
    security_id: str
    quoted_on: date
    unit: PriceUnit
    value: Decimal
    currency: Currency
    unpaid_face: Money | None = None
    accrued_per_security: Money | None = None

    def __post_init__(self) -> None:
        require_identifier(self.security_id)
        if type(self.quoted_on) is not date:
            raise ValidationError("Укажите календарную дату цены.")
        if not isinstance(self.unit, PriceUnit) or not isinstance(self.currency, Currency):
            raise ValidationError("Укажите единицу и валюту цены.")
        if not isinstance(self.value, Decimal) or not self.value.is_finite():
            raise ValidationError("Введите точное конечное значение цены.")
        exponent = self.value.as_tuple().exponent
        if (
            not isinstance(exponent, int)
            or not -8 <= exponent <= 18
            or not 0 <= self.value <= MAX_MINOR
        ):
            raise ValidationError("Цена должна быть неотрицательной, до восьми дробных знаков.")
        if self.unit is PriceUnit.CASH_PER_SECURITY:
            if self.unpaid_face is not None or self.accrued_per_security is not None:
                raise ValidationError("Денежная цена за бумагу вводится без номинала и НКД.")
        elif (
            not isinstance(self.unpaid_face, Money)
            or not isinstance(self.accrued_per_security, Money)
            or self.unpaid_face.currency != self.currency
            or self.accrued_per_security.currency != self.currency
            or self.unpaid_face.minor <= 0
            or self.accrued_per_security.minor < 0
        ):
            raise ValidationError("Укажите непогашенный номинал и НКД за бумагу в валюте цены.")


def validate_price_for_security(security: Security, price: ManualPrice) -> None:
    if not isinstance(security, Security) or not isinstance(price, ManualPrice):
        raise ValidationError("Укажите инструмент и его датированную цену.")
    if price.security_id != security.id or price.currency != security.currency:
        raise ValidationError("Цена относится к другому инструменту или валюте.")
    expected = (
        PriceUnit.PERCENT_OF_FACE
        if security.kind is SecurityKind.BOND
        else PriceUnit.CASH_PER_SECURITY
    )
    if price.unit is not expected:
        raise ValidationError(
            "Для облигации укажите процент от номинала, для акции и фонда — денежную цену."
        )


@dataclass(frozen=True, slots=True)
class QuoteInstrument:
    provider: str
    instrument_code: str
    board: str | None = None

    def __post_init__(self) -> None:
        parts: tuple[str, ...] = (self.provider, self.instrument_code)
        if self.board is not None:
            parts += (self.board,)
        if any(
            not isinstance(part, str)
            or not 1 <= len(part) <= 80
            or not part.isprintable()
            or any(character.isspace() for character in part)
            for part in parts
        ):
            raise ValidationError("Уточните код источника, инструмента или режима торгов.")


@dataclass(frozen=True, slots=True)
class MarketQuote:
    instrument: QuoteInstrument
    quoted_on: date
    unit: PriceUnit
    value: Decimal
    currency: Currency
    quoted_at: datetime | None = None
    unpaid_face: Money | None = None
    accrued_per_security: Money | None = None

    def __post_init__(self) -> None:
        if isinstance(self.quoted_at, datetime) and self.quoted_at.utcoffset() is not None:
            object.__setattr__(self, "quoted_at", self.quoted_at.astimezone(UTC))

    def price_for(self, security: Security) -> ManualPrice:
        price = ManualPrice(
            security.id,
            self.quoted_on,
            self.unit,
            self.value,
            self.currency,
            self.unpaid_face,
            self.accrued_per_security,
        )
        if price.value <= 0:
            raise ValidationError("Для рыночной оценки нужна положительная цена.")
        validate_price_for_security(security, price)
        return price


def market_value_minor(security: Security, price: ManualPrice, quantity: int) -> int:
    validate_price_for_security(security, price)
    if type(quantity) is not int or quantity < 0:
        raise ValidationError("Для оценки укажите целое неотрицательное количество бумаг.")
    numerator, denominator = price.value.as_integer_ratio()
    if price.unit is PriceUnit.PERCENT_OF_FACE:
        assert price.unpaid_face is not None and price.accrued_per_security is not None
        numerator *= price.unpaid_face.minor
        denominator *= 100
        numerator += price.accrued_per_security.minor * denominator
    else:
        numerator *= 10**price.currency.exponent
    whole, remainder = divmod(numerator * quantity, denominator)
    return whole + int(2 * remainder >= denominator)


@dataclass(frozen=True, slots=True)
class PositionResult:
    total_minor: int
    purchase_ratio_basis_points: int | None


def position_result(position: SecurityPosition, market_minor: int | None) -> PositionResult | None:
    if position.quantity and market_minor is None:
        return None
    if market_minor is not None and (type(market_minor) is not int or market_minor < 0):
        raise ValidationError("Рыночная стоимость должна быть неотрицательной целой суммой.")
    change = (
        market_minor - position.cost_minor if position.quantity and market_minor is not None else 0
    )
    total = position.realized_minor + position.distributions_minor + change
    ratio = None
    if position.purchases_minor:
        whole, remainder = divmod(abs(total) * 10000, position.purchases_minor)
        rounded = whole + int(2 * remainder >= position.purchases_minor)
        ratio = -rounded if total < 0 else rounded
    return PositionResult(total, ratio)


@dataclass(frozen=True, slots=True)
class ValuedPosition:
    position: SecurityPosition
    price: ManualPrice | None
    market_minor: int | None
    market_quote: MarketQuote | None = None

    @property
    def unrealized_minor(self) -> int | None:
        return None if self.market_minor is None else self.market_minor - self.position.cost_minor


@dataclass(frozen=True, slots=True)
class ValuationTotals:
    currency: Currency
    priced_market_minor: int
    priced_cost_minor: int
    total_cost_minor: int
    missing_count: int

    @property
    def priced_unrealized_minor(self) -> int:
        return self.priced_market_minor - self.priced_cost_minor


@dataclass(frozen=True, slots=True)
class InvestmentValuation:
    portfolio: InvestmentPortfolio
    positions: tuple[ValuedPosition, ...]
    totals: tuple[ValuationTotals, ...]


def _eligible_after_events(
    price: ManualPrice, split_on: date, amortization: BondAmortization | None
) -> bool:
    return price.quoted_on >= split_on and (
        amortization is None
        or (
            price.quoted_on >= amortization.occurred_on
            and (
                price.unit is not PriceUnit.PERCENT_OF_FACE
                or price.unpaid_face == amortization.face_after
            )
        )
    )


def value_investments(
    portfolio: InvestmentPortfolio,
    prices: Sequence[ManualPrice],
    *,
    market_quotes: Mapping[str, MarketQuote] | None = None,
) -> InvestmentValuation:
    if not isinstance(portfolio, InvestmentPortfolio):
        raise ValidationError("Укажите рассчитанный снимок портфеля.")
    holdings = {
        position.security.id: position.security
        for position in portfolio.positions
        if position.quantity
    }
    eligible: dict[str, ManualPrice] = {}
    seen: set[tuple[str, date]] = set()
    for price in prices:
        if not isinstance(price, ManualPrice):
            raise ValidationError("Укажите датированную ручную цену.")
        security = holdings.get(price.security_id)
        if security is None or price.quoted_on > portfolio.as_of:
            continue
        key = price.security_id, price.quoted_on
        if key in seen:
            raise ValidationError("У инструмента несколько ручных цен на одну дату.")
        seen.add(key)
        current = eligible.get(price.security_id)
        if current is None or current.quoted_on < price.quoted_on:
            eligible[price.security_id] = price

    latest_split: dict[tuple[str, str], date] = {}
    for event in portfolio.splits:
        position_key = event.account_id, event.security_id
        latest_split[position_key] = max(
            latest_split.get(position_key, date.min), event.occurred_on
        )
    latest_amortization = {}
    for receipt in portfolio.amortizations:
        payment = receipt.event
        payment_key = payment.account_id, payment.security_id
        # Проекция уже упорядочена по дате и порядку внутри дня.
        latest_amortization[payment_key] = payment
    values = []
    by_currency: dict[Currency, list[ValuedPosition]] = {}
    for position in portfolio.positions:
        quantity = position.quantity
        if not quantity:
            continue
        security = position.security
        quote = eligible.get(security.id)
        position_key = position.account.id, security.id
        split_on = latest_split.get(position_key, date.min)
        amortization = latest_amortization.get(position_key)
        if quote is not None and not _eligible_after_events(quote, split_on, amortization):
            quote = None
        selected_market = None
        observation = market_quotes.get(security.id) if market_quotes else None
        if observation is not None:
            try:
                market_price = observation.price_for(security)
            except ValidationError:
                market_price = None
            if (
                market_price is not None
                and market_price.quoted_on <= portfolio.as_of
                and (quote is None or market_price.quoted_on > quote.quoted_on)
                and _eligible_after_events(market_price, split_on, amortization)
            ):
                quote, selected_market = market_price, observation
        value = None if quote is None else market_value_minor(security, quote, quantity)
        row = ValuedPosition(position, quote, value, selected_market)
        values.append(row)
        by_currency.setdefault(security.currency, []).append(row)
    totals = []
    for currency in sorted(by_currency, key=lambda value: value.code):
        rows = by_currency[currency]
        priced = [
            (row.market_minor, row.position.cost_minor)
            for row in rows
            if row.market_minor is not None
        ]
        totals.append(
            ValuationTotals(
                currency=currency,
                priced_market_minor=sum(value for value, _ in priced),
                priced_cost_minor=sum(cost for _, cost in priced),
                total_cost_minor=sum(row.position.cost_minor for row in rows),
                missing_count=len(rows) - len(priced),
            )
        )
    return InvestmentValuation(portfolio, tuple(values), tuple(totals))
