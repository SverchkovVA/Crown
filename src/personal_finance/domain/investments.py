"""Снимок инвестиционного учёта с партиями FIFO и целыми денежными суммами."""

from collections import deque
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import date
from enum import StrEnum

from personal_finance.domain.errors import ValidationError
from personal_finance.domain.ledger import require_identifier
from personal_finance.domain.models import (
    MAX_MINOR,
    RUB,
    Account,
    AccountKind,
    Currency,
    Money,
    validate_name,
)


class SecurityKind(StrEnum):
    STOCK = "stock"
    BOND = "bond"
    FUND = "fund"


class TradeKind(StrEnum):
    BUY = "buy"
    SELL = "sell"
    REDEEM = "redeem"


class DistributionKind(StrEnum):
    DIVIDEND = "dividend"
    COUPON = "coupon"


@dataclass(frozen=True, slots=True)
class Security:
    id: str
    code: str
    name: str
    kind: SecurityKind
    currency: Currency = RUB

    def __post_init__(self) -> None:
        require_identifier(self.id)
        if not isinstance(self.code, str) or not isinstance(self.name, str):
            raise ValidationError("Укажите код и название инструмента.")
        code = validate_name(self.code).upper()
        if len(code) > 32 or any(character.isspace() for character in code):
            raise ValidationError("Код инструмента должен содержать до 32 символов без пробелов.")
        object.__setattr__(self, "code", code)
        object.__setattr__(self, "name", validate_name(self.name))
        if not isinstance(self.kind, SecurityKind) or not isinstance(self.currency, Currency):
            raise ValidationError("Укажите вид и валюту инструмента.")


@dataclass(frozen=True, slots=True)
class InvestmentEvent:
    id: str
    account_id: str
    security_id: str
    occurred_on: date
    sequence: int

    def __post_init__(self) -> None:
        for identifier in (self.id, self.account_id, self.security_id):
            require_identifier(identifier)
        if type(self.occurred_on) is not date:
            raise ValidationError("Укажите календарную дату инвестиционной операции.")
        if type(self.sequence) is not int or not 1 <= self.sequence <= MAX_MINOR:
            raise ValidationError("Укажите положительный порядковый номер операции за день.")


@dataclass(frozen=True, slots=True)
class SecurityTrade(InvestmentEvent):
    kind: TradeKind
    quantity: int
    amount: Money
    fee: Money
    accrued_interest: Money | None = None

    def __post_init__(self) -> None:
        InvestmentEvent.__post_init__(self)
        if not isinstance(self.kind, TradeKind):
            raise ValidationError("Выберите покупку, продажу или погашение.")
        if type(self.quantity) is not int or not 1 <= self.quantity <= MAX_MINOR:
            raise ValidationError("Укажите положительное целое количество ценных бумаг.")
        if (
            not isinstance(self.amount, Money)
            or self.amount.minor < 0
            or (self.amount.minor == 0 and self.kind is not TradeKind.REDEEM)
        ):
            raise ValidationError("Укажите сумму сделки без комиссии и НКД.")
        components = (
            (self.fee,) if self.accrued_interest is None else (self.fee, self.accrued_interest)
        )
        if any(
            not isinstance(value, Money)
            or value.currency != self.amount.currency
            or value.minor < 0
            for value in components
        ):
            raise ValidationError("Комиссия и НКД должны быть неотрицательными, в валюте сделки.")
        if abs(self.cash_delta_minor) > MAX_MINOR:
            raise ValidationError("Денежное движение сделки превышает допустимый предел.")

    @property
    def cash_delta_minor(self) -> int:
        accrued = self.accrued_interest.minor if self.accrued_interest is not None else 0
        settlement = self.amount.minor + accrued
        return (-settlement if self.kind is TradeKind.BUY else settlement) - self.fee.minor


@dataclass(frozen=True, slots=True)
class SecurityDistribution(InvestmentEvent):
    kind: DistributionKind
    received: Money

    def __post_init__(self) -> None:
        InvestmentEvent.__post_init__(self)
        if not isinstance(self.kind, DistributionKind):
            raise ValidationError("Выберите дивиденды или купон.")
        if not isinstance(self.received, Money) or self.received.minor <= 0:
            raise ValidationError("Укажите положительную зачисленную сумму выплаты.")


PortfolioEvent = SecurityTrade | SecurityDistribution


@dataclass(frozen=True, slots=True)
class StockSplit(InvestmentEvent):
    multiplier: int

    def __post_init__(self) -> None:
        InvestmentEvent.__post_init__(self)
        if type(self.multiplier) is not int or not 2 <= self.multiplier <= MAX_MINOR:
            raise ValidationError("Коэффициент дробления должен быть целым числом от двух.")


@dataclass(frozen=True, slots=True)
class OpenLot:
    acquisition_id: str
    acquired_on: date
    quantity: int
    cost_minor: int


@dataclass(frozen=True, slots=True)
class FifoAllocation:
    acquisition_id: str
    quantity: int
    cost_minor: int


@dataclass(frozen=True, slots=True)
class Disposal:
    trade: SecurityTrade
    allocations: tuple[FifoAllocation, ...]

    @property
    def cost_minor(self) -> int:
        return sum(row.cost_minor for row in self.allocations)

    @property
    def realized_minor(self) -> int:
        return self.trade.cash_delta_minor - self.cost_minor


@dataclass(frozen=True, slots=True)
class SecurityPosition:
    account: Account
    security: Security
    lots: tuple[OpenLot, ...]
    realized_minor: int
    distributions_minor: int
    purchases_minor: int

    @property
    def quantity(self) -> int:
        return sum(lot.quantity for lot in self.lots)

    @property
    def cost_minor(self) -> int:
        return sum(lot.cost_minor for lot in self.lots)


@dataclass(frozen=True, slots=True)
class InvestmentPortfolio:
    as_of: date
    positions: tuple[SecurityPosition, ...]
    disposals: tuple[Disposal, ...]
    distributions: tuple[SecurityDistribution, ...]
    splits: tuple[StockSplit, ...] = ()


@dataclass(slots=True)
class _Lot:
    acquisition_id: str
    acquired_on: date
    quantity: int
    cost_minor: int
    sold: int = 0

    def _cumulative_cost(self, quantity: int) -> int:
        whole, rest = divmod(self.cost_minor * quantity, self.quantity)
        return whole + int(2 * rest >= self.quantity)

    def allocate(self, quantity: int) -> FifoAllocation:
        before = self._cumulative_cost(self.sold)
        self.sold += quantity
        return FifoAllocation(
            self.acquisition_id, quantity, self._cumulative_cost(self.sold) - before
        )

    def open_lot(self) -> OpenLot:
        return OpenLot(
            self.acquisition_id,
            self.acquired_on,
            self.quantity - self.sold,
            self.cost_minor - self._cumulative_cost(self.sold),
        )

    def split(self, multiplier: int) -> None:
        remaining = self.open_lot()
        self.quantity = remaining.quantity * multiplier
        self.cost_minor = remaining.cost_minor
        self.sold = 0


@dataclass(slots=True)
class _Position:
    lots: deque[_Lot] = field(default_factory=deque)
    quantity: int = 0
    realized_minor: int = 0
    distributions_minor: int = 0
    purchases_minor: int = 0

    def dispose(self, trade: SecurityTrade) -> Disposal:
        if trade.quantity > self.quantity:
            raise ValidationError("На дату продажи или погашения на этом счёте недостаточно бумаг.")
        remaining = trade.quantity
        allocations = []
        while remaining:
            lot = self.lots[0]
            taken = min(remaining, lot.quantity - lot.sold)
            allocations.append(lot.allocate(taken))
            remaining -= taken
            if lot.sold == lot.quantity:
                self.lots.popleft()
        self.quantity -= trade.quantity
        disposal = Disposal(trade, tuple(allocations))
        self.realized_minor += disposal.realized_minor
        return disposal

    def split(self, event: StockSplit) -> None:
        quantity = self.quantity * event.multiplier
        if not 1 <= quantity <= MAX_MINOR:
            raise ValidationError("Дробление требует открытых акций в допустимом количестве.")
        for lot in self.lots:
            lot.split(event.multiplier)
        self.quantity = quantity


def _check_security(event: PortfolioEvent, account: Account, security: Security) -> None:
    currency = (
        event.amount.currency if isinstance(event, SecurityTrade) else event.received.currency
    )
    if currency != security.currency or currency.code != account.currency:
        raise ValidationError("Валюты инвестиционной операции, бумаги и счёта должны совпадать.")
    bond = security.kind is SecurityKind.BOND
    if isinstance(event, SecurityTrade):
        if event.kind is TradeKind.REDEEM and not bond:
            raise ValidationError("Погашение предусмотрено для облигаций.")
        if bond != (event.accrued_interest is not None):
            raise ValidationError("Укажите НКД для облигации. У акции и фонда НКД отсутствует.")
    elif bond != (event.kind is DistributionKind.COUPON):
        raise ValidationError(
            "Купоны относятся к облигациям. Для акции или фонда выберите дивиденды."
        )


def project_investments(
    accounts: Sequence[Account],
    securities: Sequence[Security],
    events: Sequence[PortfolioEvent],
    as_of: date,
    *,
    splits: Sequence[StockSplit] = (),
) -> InvestmentPortfolio:
    if type(as_of) is not date:
        raise ValidationError("Укажите календарную дату инвестиционного отчёта.")
    account_by_id: dict[str, Account] = {}
    for account in accounts:
        if not isinstance(account, Account) or account.kind is not AccountKind.INVESTMENT:
            raise ValidationError("Выберите инвестиционные счета.")
        require_identifier(account.id)
        if account.id in account_by_id:
            raise ValidationError("Инвестиционный счёт повторяется в отчёте.")
        account_by_id[account.id] = account
    security_by_id: dict[str, Security] = {}
    currencies: dict[str, Currency] = {}
    for security in securities:
        if not isinstance(security, Security):
            raise ValidationError("Укажите данные инструмента.")
        if security.id in security_by_id:
            raise ValidationError("Инструмент повторяется в отчёте.")
        currency = security.currency
        if currencies.setdefault(currency.code, currency) != currency:
            raise ValidationError("Одинаковый код валюты указан с разной точностью.")
        security_by_id[security.id] = security

    event_ids: set[str] = set()
    orders: set[tuple[str, date, int]] = set()
    current: list[PortfolioEvent | StockSplit] = []
    if any(not isinstance(event, (SecurityTrade, SecurityDistribution)) for event in events):
        raise ValidationError("Укажите сделку или денежную выплату.")
    if any(not isinstance(split, StockSplit) for split in splits):
        raise ValidationError("Укажите событие дробления акций.")
    for event in (*events, *splits):
        matched_account = account_by_id.get(event.account_id)
        matched_security = security_by_id.get(event.security_id)
        if matched_account is None or matched_security is None:
            raise ValidationError("Для операции отсутствует счёт или инструмент.")
        if isinstance(event, StockSplit):
            if matched_security.kind is not SecurityKind.STOCK:
                raise ValidationError("Дробление предусмотрено для акций.")
            if matched_account.currency != matched_security.currency.code:
                raise ValidationError("Валюты акции и счёта должны совпадать.")
        else:
            _check_security(event, matched_account, matched_security)
        order = event.account_id, event.occurred_on, event.sequence
        if event.id in event_ids or order in orders:
            raise ValidationError("Операция или её порядковый номер за день повторяется.")
        event_ids.add(event.id)
        orders.add(order)
        if event.occurred_on <= as_of:
            current.append(event)

    positions: dict[tuple[str, str], _Position] = {}
    disposals = []
    distributions = []
    applied_splits = []
    for event in sorted(current, key=lambda row: (row.occurred_on, row.account_id, row.sequence)):
        key = event.account_id, event.security_id
        position = positions.get(key)
        if position is None:
            position = positions[key] = _Position()
        if isinstance(event, StockSplit):
            position.split(event)
            applied_splits.append(event)
        elif isinstance(event, SecurityDistribution):
            position.distributions_minor += event.received.minor
            distributions.append(event)
        elif event.kind is TradeKind.BUY:
            position.lots.append(
                _Lot(event.id, event.occurred_on, event.quantity, -event.cash_delta_minor)
            )
            position.quantity += event.quantity
            position.purchases_minor -= event.cash_delta_minor
        else:
            disposals.append(position.dispose(event))

    return InvestmentPortfolio(
        as_of,
        tuple(
            SecurityPosition(
                account_by_id[account_id],
                security_by_id[security_id],
                tuple(lot.open_lot() for lot in position.lots),
                position.realized_minor,
                position.distributions_minor,
                position.purchases_minor,
            )
            for (account_id, security_id), position in sorted(positions.items())
        ),
        tuple(disposals),
        tuple(distributions),
        tuple(applied_splits),
    )
