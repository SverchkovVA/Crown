"""Кандидаты денежных движений для событий сохранённого портфеля."""

from collections import Counter, defaultdict
from collections.abc import Sequence
from dataclasses import dataclass
from enum import StrEnum

from personal_finance.domain.analytics import ReportPeriod, ReportScope
from personal_finance.domain.analytics_cashflow import CashMovement, cash_flow_report
from personal_finance.domain.investment_portfolio import SavedPortfolioReport
from personal_finance.domain.investments import PortfolioEvent, SecurityTrade
from personal_finance.domain.ledger import Operation, OperationKind
from personal_finance.domain.models import Account, Currency


class InvestmentCashStatus(StrEnum):
    NO_MOVEMENT = "no_movement"
    MISSING = "missing"
    UNIQUE = "unique"
    AMBIGUOUS = "ambiguous"


@dataclass(frozen=True, slots=True)
class InvestmentCashRow:
    account: Account
    security_code: str
    currency: Currency
    event: PortfolioEvent
    minor: int
    status: InvestmentCashStatus
    candidates: tuple[CashMovement, ...]


def reconcile_investment_cash(
    portfolio: SavedPortfolioReport, operations: Sequence[Operation]
) -> tuple[InvestmentCashRow, ...]:
    accounts = frozenset(row.position.account.id for row in portfolio.rows)
    if not accounts:
        return ()
    period = ReportPeriod(portfolio.as_of, portfolio.as_of)
    cash = cash_flow_report(operations, period, ReportScope(account_ids=accounts))
    movements = defaultdict(list)
    for movement in cash.movements:
        if movement.operation.draft.kind in (OperationKind.INCOME, OperationKind.EXPENSE):
            key = (
                movement.account_id,
                movement.operation.draft.occurred_on,
                movement.currency,
                movement.minor,
            )
            movements[key].append(movement)
    events = [
        (
            row,
            event,
            event.cash_delta_minor if isinstance(event, SecurityTrade) else event.received.minor,
        )
        for row in portfolio.rows
        for event in row.record.inputs.events
        if event.occurred_on <= portfolio.as_of
    ]
    counts = Counter(
        (event.account_id, event.occurred_on, row.position.security.currency, minor)
        for row, event, minor in events
        if minor
    )
    result = []
    for row, event, minor in events:
        currency = row.position.security.currency
        key = event.account_id, event.occurred_on, currency, minor
        candidates = tuple(movements.get(key, ())) if minor else ()
        if not minor:
            status = InvestmentCashStatus.NO_MOVEMENT
        elif not candidates:
            status = InvestmentCashStatus.MISSING
        elif len(candidates) == counts[key] == 1:
            status = InvestmentCashStatus.UNIQUE
        else:
            status = InvestmentCashStatus.AMBIGUOUS
        result.append(
            InvestmentCashRow(
                row.position.account,
                row.position.security.code,
                currency,
                event,
                minor,
                status,
                candidates,
            )
        )
    return tuple(
        sorted(
            result,
            key=lambda row: (
                row.event.occurred_on,
                row.account.name.casefold(),
                row.security_code,
                row.event.sequence,
                row.event.id,
            ),
            reverse=True,
        )
    )
