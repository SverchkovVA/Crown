"""Канонические условия сохранённой инвестиционной позиции."""

import json
from dataclasses import dataclass
from datetime import date
from decimal import Decimal, InvalidOperation
from typing import Any

from personal_finance.domain.errors import ValidationError
from personal_finance.domain.investment_prices import (
    ManualPrice,
    PriceUnit,
    validate_price_for_security,
)
from personal_finance.domain.investments import (
    DistributionKind,
    InvestmentPortfolio,
    PortfolioEvent,
    Security,
    SecurityDistribution,
    SecurityKind,
    SecurityTrade,
    StockSplit,
    TradeKind,
    project_investments,
)
from personal_finance.domain.ledger import require_identifier
from personal_finance.domain.models import Account, Currency, Money

MAX_POSITION_PAYLOAD_BYTES = 4 * 1024 * 1024
MAX_POSITION_EVENTS = 10000
MAX_POSITION_PRICES = 10000
_TRADE_KINDS = frozenset(kind.value for kind in TradeKind)


@dataclass(frozen=True, slots=True)
class InvestmentPositionInputs:
    account_id: str
    security: Security
    events: tuple[PortfolioEvent, ...]
    prices: tuple[ManualPrice, ...] = ()
    splits: tuple[StockSplit, ...] = ()

    def __post_init__(self) -> None:
        require_identifier(self.account_id)
        if not isinstance(self.security, Security):
            raise ValidationError("Укажите инструмент инвестиционной позиции.")
        if not isinstance(self.events, tuple) or not 1 <= len(self.events) <= MAX_POSITION_EVENTS:
            raise ValidationError("Для сохранения позиции добавьте от 1 до 10 000 операций.")
        if any(
            not isinstance(event, (SecurityTrade, SecurityDistribution))
            or event.account_id != self.account_id
            or event.security_id != self.security.id
            for event in self.events
        ):
            raise ValidationError("Операции должны относиться к одному счёту и инструменту.")
        if (
            not isinstance(self.splits, tuple)
            or len(self.events) + len(self.splits) > MAX_POSITION_EVENTS
            or (self.splits and self.security.kind is not SecurityKind.STOCK)
            or any(
                not isinstance(split, StockSplit)
                or split.account_id != self.account_id
                or split.security_id != self.security.id
                for split in self.splits
            )
        ):
            raise ValidationError("Проверьте дробления и общее количество событий позиции.")
        if not isinstance(self.prices, tuple) or len(self.prices) > MAX_POSITION_PRICES:
            raise ValidationError("Для позиции можно хранить до 10 000 ручных цен.")
        dates: set[date] = set()
        for price in self.prices:
            validate_price_for_security(self.security, price)
            if price.quoted_on in dates:
                raise ValidationError("На одну дату можно сохранить одну ручную цену.")
            dates.add(price.quoted_on)
        object.__setattr__(
            self,
            "events",
            tuple(sorted(self.events, key=lambda row: (row.occurred_on, row.sequence))),
        )
        object.__setattr__(
            self, "prices", tuple(sorted(self.prices, key=lambda price: price.quoted_on))
        )
        object.__setattr__(
            self,
            "splits",
            tuple(sorted(self.splits, key=lambda row: (row.occurred_on, row.sequence))),
        )

    def check_for(self, account: Account) -> InvestmentPortfolio:
        if not isinstance(account, Account) or account.id != self.account_id:
            raise ValidationError("Инвестиционная позиция относится к другому счёту.")
        return project_investments(
            (account,), (self.security,), self.events, date.max, splits=self.splits
        )


@dataclass(frozen=True, slots=True)
class SavedInvestmentPosition:
    id: str
    revision_id: str
    inputs: InvestmentPositionInputs
    is_deleted: bool
    created_at: str


def _event_payload(event: PortfolioEvent) -> dict[str, Any]:
    result: dict[str, Any] = {
        "id": event.id,
        "on": event.occurred_on.isoformat(),
        "sequence": event.sequence,
        "kind": event.kind.value,
    }
    if isinstance(event, SecurityTrade):
        result.update(
            quantity=event.quantity,
            amount_minor=event.amount.minor,
            fee_minor=event.fee.minor,
            accrued_minor=event.accrued_interest.minor if event.accrued_interest else None,
        )
    else:
        result["received_minor"] = event.received.minor
    return result


def _decimal_text(value: Decimal) -> str:
    if not value:
        return "0"
    text = format(value, "f")
    return text.rstrip("0").rstrip(".") if "." in text else text


def _price_payload(price: ManualPrice) -> dict[str, Any]:
    return {
        "on": price.quoted_on.isoformat(),
        "unit": price.unit.value,
        "value": _decimal_text(price.value),
        "face_minor": price.unpaid_face.minor if price.unpaid_face else None,
        "accrued_minor": price.accrued_per_security.minor if price.accrued_per_security else None,
    }


def investment_position_json(
    inputs: InvestmentPositionInputs, *, allow_splits: bool = False
) -> str:
    if inputs.splits and not allow_splits:
        raise ValidationError("Для сохранения дробления нужна совместимая схема книги.")
    security = inputs.security
    split_payload = (
        {
            "splits": [
                {
                    "id": event.id,
                    "on": event.occurred_on.isoformat(),
                    "sequence": event.sequence,
                    "multiplier": event.multiplier,
                }
                for event in inputs.splits
            ]
        }
        if inputs.splits
        else {}
    )
    raw = json.dumps(
        {
            "format": 2 if inputs.splits else 1,
            "account_id": inputs.account_id,
            "security": {
                "id": security.id,
                "code": security.code,
                "name": security.name,
                "kind": security.kind.value,
                "currency": {
                    "code": security.currency.code,
                    "exponent": security.currency.exponent,
                },
            },
            "events": [_event_payload(event) for event in inputs.events],
            "prices": [_price_payload(price) for price in inputs.prices],
            **split_payload,
        },
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )
    if len(raw.encode("utf-8")) > MAX_POSITION_PAYLOAD_BYTES:
        raise ValidationError("Размер инвестиционной позиции превышает допустимый предел.")
    return raw


def _event_from_json(raw: dict[str, Any], account_id: str, security: Security) -> PortfolioEvent:
    fields = (raw["id"], account_id, security.id, date.fromisoformat(raw["on"]), raw["sequence"])
    currency = security.currency
    if raw["kind"] in _TRADE_KINDS:
        accrued = raw["accrued_minor"]
        return SecurityTrade(
            *fields,
            TradeKind(raw["kind"]),
            raw["quantity"],
            Money(raw["amount_minor"], currency),
            Money(raw["fee_minor"], currency),
            None if accrued is None else Money(accrued, currency),
        )
    return SecurityDistribution(
        *fields, DistributionKind(raw["kind"]), Money(raw["received_minor"], currency)
    )


def _price_from_json(raw: dict[str, Any], security: Security) -> ManualPrice:
    value = raw["value"]
    if not isinstance(value, str):
        raise TypeError
    currency = security.currency
    face = raw["face_minor"]
    accrued = raw["accrued_minor"]
    return ManualPrice(
        security.id,
        date.fromisoformat(raw["on"]),
        PriceUnit(raw["unit"]),
        Decimal(value),
        currency,
        None if face is None else Money(face, currency),
        None if accrued is None else Money(accrued, currency),
    )


def investment_position_from_json(
    raw: str, *, allow_splits: bool = False
) -> InvestmentPositionInputs:
    try:
        if not isinstance(raw, str) or len(raw.encode("utf-8")) > MAX_POSITION_PAYLOAD_BYTES:
            raise ValueError
        data = json.loads(raw)
        version = data["format"]
        if (
            type(version) is not int
            or (version != 1 and not (version == 2 and allow_splits))
            or not isinstance(data["events"], list)
            or not isinstance(data["prices"], list)
        ):
            raise ValueError
        instrument = data["security"]
        currency = instrument["currency"]
        security = Security(
            instrument["id"],
            instrument["code"],
            instrument["name"],
            SecurityKind(instrument["kind"]),
            Currency(currency["code"], currency["exponent"]),
        )
        account_id = data["account_id"]
        splits = data["splits"] if version == 2 else []
        if not isinstance(splits, list):
            raise ValueError
        inputs = InvestmentPositionInputs(
            account_id,
            security,
            tuple(_event_from_json(event, account_id, security) for event in data["events"]),
            tuple(_price_from_json(price, security) for price in data["prices"]),
            tuple(
                StockSplit(
                    event["id"],
                    account_id,
                    security.id,
                    date.fromisoformat(event["on"]),
                    event["sequence"],
                    event["multiplier"],
                )
                for event in splits
            ),
        )
        if investment_position_json(inputs, allow_splits=allow_splits) != raw:
            raise ValueError
        return inputs
    except (
        ValueError,
        TypeError,
        KeyError,
        AttributeError,
        InvalidOperation,
        ValidationError,
        RecursionError,
    ):
        raise ValidationError("Некорректный формат сохранённой инвестиционной позиции.") from None
