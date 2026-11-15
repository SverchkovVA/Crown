"""Локальный кэш подтверждённых котировок в зашифрованной книге."""

from collections.abc import Sequence
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from threading import RLock

from sqlalchemy import Connection, Engine, text
from sqlalchemy.engine import RowMapping

from personal_finance.application.market_price_refresh import (
    MarketQuote,
    QuoteBinding,
    QuoteInstrument,
    QuoteRefreshRow,
    QuoteStatus,
    compare_market_quotes,
)
from personal_finance.domain.errors import ValidationError, VaultError
from personal_finance.domain.investment_prices import PriceUnit
from personal_finance.domain.ledger import require_identifier
from personal_finance.domain.models import MAX_MINOR, Currency, Money

_SELECT_LATEST = text("""
    SELECT quoted_on, quoted_at, unit, value, currency, exponent, unpaid_face_minor, accrued_minor
      FROM market_quote_cache
     WHERE book_id = :book_id AND provider = :provider AND instrument_code = :instrument_code
       AND board = :board AND quoted_on <= :through
     ORDER BY quoted_on DESC
     LIMIT 1
""")
_SAVE = text("""
    INSERT INTO market_quote_cache (
        book_id, provider, instrument_code, board, quoted_on, quoted_at, unit, value,
        currency, exponent, unpaid_face_minor, accrued_minor
    ) VALUES (
        :book_id, :provider, :instrument_code, :board, :quoted_on, :quoted_at, :unit,
        :value, :currency, :exponent, :unpaid_face_minor, :accrued_minor
    )
    ON CONFLICT (book_id, provider, instrument_code, board, quoted_on) DO UPDATE SET
        quoted_at = excluded.quoted_at, value = excluded.value, unit = excluded.unit,
        currency = excluded.currency, exponent = excluded.exponent,
        unpaid_face_minor = excluded.unpaid_face_minor, accrued_minor = excluded.accrued_minor
""")


def _decode(instrument: QuoteInstrument, row: RowMapping) -> MarketQuote:
    try:
        traded_at = datetime.fromisoformat(row["quoted_at"]) if row["quoted_at"] else None
        traded_on = date.fromisoformat(row["quoted_on"])
        value = Decimal(row["value"])
        exponent = value.as_tuple().exponent
        if (
            traded_on.isoformat() != row["quoted_on"]
            or (traded_at is not None and traded_at.isoformat() != row["quoted_at"])
            or not value.is_finite()
            or not 0 < value <= MAX_MINOR
            or not isinstance(exponent, int)
            or not -8 <= exponent <= 18
            or format(value, "f") != row["value"]
        ):
            raise ValueError
        currency = Currency(row["currency"], row["exponent"])
        face = row["unpaid_face_minor"]
        accrued = row["accrued_minor"]
        return MarketQuote(
            instrument,
            traded_on,
            PriceUnit(row["unit"]),
            value,
            currency,
            traded_at,
            None if face is None else Money(face, currency),
            None if accrued is None else Money(accrued, currency),
        )
    except (TypeError, ValueError, InvalidOperation, ValidationError) as error:
        raise VaultError("Сохранённая котировка повреждена.") from error


class SqlMarketQuoteCache:
    def __init__(self, engine: Engine, book_id: str, mutex: RLock) -> None:
        require_identifier(book_id)
        self._engine = engine
        self._book_id = book_id
        self._mutex = mutex

    def _scope(self, instrument: QuoteInstrument) -> dict[str, str]:
        return {
            "book_id": self._book_id,
            "provider": instrument.provider,
            "instrument_code": instrument.instrument_code,
            "board": instrument.board or "",
        }

    def _latest(self, conn: Connection, binding: QuoteBinding, through: date) -> MarketQuote | None:
        record = (
            conn.execute(
                _SELECT_LATEST, {**self._scope(binding.instrument), "through": through.isoformat()}
            )
            .mappings()
            .first()
        )
        if record is None:
            return None
        quote = _decode(binding.instrument, record)
        quote.price_for(binding.security)
        return quote

    def latest(self, binding: QuoteBinding, through: date = date.max) -> MarketQuote | None:
        return self.snapshot((binding,), through).get(binding.security.id)

    def snapshot(
        self, bindings: Sequence[QuoteBinding], through: date = date.max
    ) -> dict[str, MarketQuote]:
        if type(through) is not date:
            raise ValidationError("Для выбора котировки нужна календарная дата.")
        result = {}
        with self._mutex, self._engine.connect() as conn:
            for binding in bindings:
                if (quote := self._latest(conn, binding, through)) is not None:
                    result[binding.security.id] = quote
        return result

    def check_integrity(self) -> None:
        with self._mutex, self._engine.connect() as conn:
            rows = conn.execute(
                text("SELECT * FROM market_quote_cache WHERE book_id=:book_id"),
                {"book_id": self._book_id},
            ).mappings()
            for row in rows:
                try:
                    instrument = QuoteInstrument(
                        row["provider"], row["instrument_code"], row["board"] or None
                    )
                except ValidationError as error:
                    raise VaultError("Привязка сохранённой котировки повреждена.") from error
                _decode(instrument, row)

    def save_updates(self, rows: Sequence[QuoteRefreshRow]) -> tuple[QuoteRefreshRow, ...]:
        if not rows:
            return ()
        result = []
        with self._mutex, self._engine.begin() as conn:
            for row in rows:
                current = self._latest(conn, row.binding, date.max)
                if row.status is not QuoteStatus.UPDATED:
                    result.append(QuoteRefreshRow(row.binding, row.status, current))
                    continue
                quote = row.current
                if quote is None or quote.instrument != row.binding.instrument:
                    raise ValidationError("Котировка относится к другому инструменту или режиму.")
                price = quote.price_for(row.binding.security)
                if quote.quoted_at is not None and quote.quoted_at.utcoffset() is None:
                    raise ValidationError("Время котировки должно содержать часовой пояс.")
                status = compare_market_quotes(quote, current)
                if status is QuoteStatus.UPDATED:
                    conn.execute(
                        _SAVE,
                        {
                            **self._scope(quote.instrument),
                            "quoted_on": quote.quoted_on.isoformat(),
                            "quoted_at": quote.quoted_at.isoformat() if quote.quoted_at else None,
                            "unit": price.unit.value,
                            "value": format(price.value, "f"),
                            "currency": price.currency.code,
                            "exponent": price.currency.exponent,
                            "unpaid_face_minor": price.unpaid_face.minor
                            if price.unpaid_face
                            else None,
                            "accrued_minor": (
                                price.accrued_per_security.minor
                                if price.accrued_per_security
                                else None
                            ),
                        },
                    )
                    current = quote
                result.append(QuoteRefreshRow(row.binding, status, current))
        return tuple(result)
