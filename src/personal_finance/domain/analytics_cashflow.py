"""Остатки и движения активов одного снимка, без записи и пересчёта валют."""

from collections.abc import Sequence
from dataclasses import dataclass
from enum import StrEnum

from personal_finance.domain.analytics import ReportPeriod, ReportScope
from personal_finance.domain.errors import ValidationError
from personal_finance.domain.ledger import LedgerRole, Operation, OperationKind, require_identifier
from personal_finance.domain.models import Currency


@dataclass(frozen=True, slots=True)
class CashPosition:
    currency: Currency
    opening_minor: int = 0
    capital_minor: int = 0
    incoming_minor: int = 0
    outgoing_minor: int = 0

    @property
    def change_minor(self) -> int:
        return self.capital_minor + self.incoming_minor - self.outgoing_minor

    @property
    def closing_minor(self) -> int:
        return self.opening_minor + self.change_minor


@dataclass(frozen=True, slots=True)
class AccountCashPosition:
    account_id: str
    totals: CashPosition


@dataclass(frozen=True, slots=True)
class CashMovement:
    operation: Operation
    account_id: str
    minor: int
    currency: Currency

    @property
    def fee_minor(self) -> int:
        value = self.operation.draft
        return value.fee.minor if self.account_id == value.account_id else 0


class CashSourceKind(StrEnum):
    BEFORE = "before"
    PERIOD = "period"
    THROUGH_END = "through_end"


@dataclass(frozen=True, slots=True)
class CashFlowReport:
    period: ReportPeriod
    scope: ReportScope
    totals: tuple[CashPosition, ...]
    accounts: tuple[AccountCashPosition, ...]
    movements: tuple[CashMovement, ...]

    @property
    def operation_references(self) -> tuple[tuple[str, str], ...]:
        return tuple(sorted({(v.operation.id, v.operation.revision_id) for v in self.movements}))

    @property
    def period_operation_count(self) -> int:
        return len(
            {
                v.operation.id
                for v in self.movements
                if v.operation.draft.occurred_on >= self.period.start
            }
        )


def cash_flow_report(
    operations: Sequence[Operation], period: ReportPeriod, scope: ReportScope | None = None
) -> CashFlowReport:
    if not isinstance(period, ReportPeriod):
        raise ValidationError("Укажите период движения средств.")
    if scope is None:
        scope = ReportScope()
    if not isinstance(scope, ReportScope) or scope.category_ids is not None:
        raise ValidationError("Для движения средств выберите счета без фильтра категорий.")
    currencies: dict[str, Currency] = {}
    account_currencies: dict[str, Currency] = {}
    totals: dict[Currency, list[int]] = {}
    accounts: dict[str, list[int]] = {}
    movements = []
    seen: set[str] = set()
    for operation in operations:
        if not isinstance(operation, Operation):
            raise ValidationError("Отчёт принимает только текущие финансовые операции.")
        require_identifier(operation.id)
        require_identifier(operation.revision_id)
        if operation.id in seen:
            raise ValidationError("Передайте только текущую версию операции без повторов.")
        seen.add(operation.id)
        value = operation.draft
        if operation.is_deleted or value.occurred_on > period.end:
            continue
        for posting in value.postings():
            if posting.role != LedgerRole.ASSET:
                continue
            account_id = posting.reference_id
            if account_id is None:
                raise ValidationError("У движения отсутствует счёт.")
            require_identifier(account_id)
            if scope.account_ids is not None and account_id not in scope.account_ids:
                continue
            currency = posting.amount.currency
            if currency.code in currencies and currencies[currency.code] != currency:
                raise ValidationError("Определения одной валюты различаются.")
            if account_id in account_currencies and account_currencies[account_id] != currency:
                raise ValidationError("У одного счёта обнаружены разные валюты.")
            currencies[currency.code] = currency
            account_currencies[account_id] = currency
            if value.occurred_on < period.start:
                index, amount = 0, posting.amount.minor
            elif value.kind == OperationKind.OPENING:
                index, amount = 1, posting.amount.minor
            elif posting.amount.minor > 0:
                index, amount = 2, posting.amount.minor
            else:
                index, amount = 3, -posting.amount.minor
            totals.setdefault(currency, [0, 0, 0, 0])[index] += amount
            accounts.setdefault(account_id, [0, 0, 0, 0])[index] += amount
            movements.append(CashMovement(operation, account_id, posting.amount.minor, currency))
    return CashFlowReport(
        period,
        scope,
        tuple(CashPosition(c, *totals[c]) for c in sorted(totals, key=lambda c: c.code)),
        tuple(
            AccountCashPosition(i, CashPosition(account_currencies[i], *accounts[i]))
            for i in sorted(accounts)
        ),
        tuple(
            sorted(
                movements,
                key=lambda v: (v.operation.draft.occurred_on, v.operation.id, v.account_id),
            )
        ),
    )


def cash_flow_sources(
    report: CashFlowReport,
    currency: Currency,
    kind: CashSourceKind,
    *,
    account_id: str | None = None,
) -> tuple[CashMovement, ...]:
    if not isinstance(currency, Currency) or currency not in {v.currency for v in report.totals}:
        raise ValidationError("Валюты нет в этом отчёте.")
    if not isinstance(kind, CashSourceKind):
        raise ValidationError("Выберите часть движения средств.")
    if account_id is not None:
        require_identifier(account_id)
        if not any(
            v.account_id == account_id and v.totals.currency == currency for v in report.accounts
        ):
            raise ValidationError("Счёта нет в выбранной валюте отчёта.")
    return tuple(
        v
        for v in report.movements
        if v.currency == currency
        and (account_id is None or v.account_id == account_id)
        and (
            kind == CashSourceKind.THROUGH_END
            or kind == CashSourceKind.BEFORE
            and v.operation.draft.occurred_on < report.period.start
            or kind == CashSourceKind.PERIOD
            and v.operation.draft.occurred_on >= report.period.start
        )
    )
