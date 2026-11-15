"""Подготовка частичного обновления рыночных цен без зависимости от поставщика."""

from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime
from decimal import Decimal
from enum import StrEnum

from personal_finance.domain.errors import ValidationError
from personal_finance.domain.investment_prices import (
    ManualPrice,
    PriceUnit,
    validate_price_for_security,
)
from personal_finance.domain.investments import Security
from personal_finance.domain.models import Currency, Money


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
class QuoteBinding:
    security: Security
    instrument: QuoteInstrument


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
        if self.quoted_at is not None and _aware(self.quoted_at):
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


class QuoteStatus(StrEnum):
    UPDATED = "updated"
    CURRENT = "current"
    MISSING = "missing"
    INCOMPATIBLE = "incompatible"
    AMBIGUOUS = "ambiguous"
    OLDER = "older"
    SOURCE_UNAVAILABLE = "source_unavailable"


@dataclass(frozen=True, slots=True)
class QuoteRefreshRow:
    binding: QuoteBinding
    status: QuoteStatus
    current: MarketQuote | None


QuoteProvider = Callable[[tuple[QuoteInstrument, ...]], Iterable[MarketQuote]]


def _aware(value: datetime) -> bool:
    return isinstance(value, datetime) and value.utcoffset() is not None


def _status(
    binding: QuoteBinding,
    rows: Sequence[MarketQuote],
    previous: MarketQuote | None,
    as_of: date,
    fetched_at: datetime,
) -> QuoteStatus:
    if not rows:
        return QuoteStatus.MISSING
    for observation in rows:
        try:
            observation.price_for(binding.security)
        except ValidationError:
            return QuoteStatus.INCOMPATIBLE
        if observation.quoted_on > as_of or (
            observation.quoted_at is not None
            and (not _aware(observation.quoted_at) or observation.quoted_at > fetched_at)
        ):
            return QuoteStatus.INCOMPATIBLE
    candidate = rows[0]
    if any(value != candidate for value in rows[1:]):
        return QuoteStatus.AMBIGUOUS
    if previous is None or candidate.quoted_on > previous.quoted_on:
        return QuoteStatus.UPDATED
    if candidate == previous:
        return QuoteStatus.CURRENT
    if candidate.quoted_on < previous.quoted_on:
        return QuoteStatus.OLDER
    if candidate.quoted_at is None or previous.quoted_at is None:
        return QuoteStatus.AMBIGUOUS
    if candidate.quoted_at > previous.quoted_at:
        return QuoteStatus.UPDATED
    if candidate.quoted_at < previous.quoted_at:
        return QuoteStatus.OLDER
    return QuoteStatus.AMBIGUOUS


def refresh_market_prices(
    provider: QuoteProvider,
    bindings: Sequence[QuoteBinding],
    previous: Mapping[str, MarketQuote],
    *,
    as_of: date,
    fetched_at: datetime,
) -> tuple[QuoteRefreshRow, ...]:
    """Вызывает источник только по явному запросу сценария обновления."""
    if type(as_of) is not date or not _aware(fetched_at):
        raise ValidationError("Для запроса цен нужны дата расчёта и момент с часовым поясом.")
    fetched_at = fetched_at.astimezone(UTC)
    if not bindings:
        return ()
    if any(
        not isinstance(binding, QuoteBinding)
        or not isinstance(binding.security, Security)
        or not isinstance(binding.instrument, QuoteInstrument)
        for binding in bindings
    ):
        raise ValidationError("Для каждой цены выберите инструмент и его код у источника.")
    if len({binding.instrument.provider for binding in bindings}) != 1:
        raise ValidationError("Один запрос цен относится к одному источнику.")
    requests: dict[QuoteInstrument, list[MarketQuote]] = {}
    existing: dict[str, MarketQuote] = {}
    seen: set[str] = set()
    for binding in bindings:
        if binding.security.id in seen:
            raise ValidationError("Выберите один источник и режим для каждого инструмента.")
        seen.add(binding.security.id)
        requests.setdefault(binding.instrument, [])
        cached = previous.get(binding.security.id)
        if cached is not None and cached.instrument == binding.instrument:
            cached.price_for(binding.security)
            if cached.quoted_at is not None and not _aware(cached.quoted_at):
                raise ValidationError("У сохранённой цены отсутствует часовой пояс.")
            existing[binding.security.id] = cached
    source_failed = False
    try:
        for quote in provider(tuple(requests)):
            if (
                isinstance(quote, MarketQuote)
                and isinstance(quote.instrument, QuoteInstrument)
                and quote.instrument in requests
            ):
                requests[quote.instrument].append(quote)
    except (OSError, ValidationError):
        source_failed = True
    result = []
    for binding in bindings:
        old = existing.get(binding.security.id)
        choices = requests[binding.instrument]
        status = (
            QuoteStatus.SOURCE_UNAVAILABLE
            if source_failed and not choices
            else _status(binding, choices, old, as_of, fetched_at)
        )
        latest = choices[0] if status is QuoteStatus.UPDATED else old
        result.append(QuoteRefreshRow(binding, status, latest))
    return tuple(result)
