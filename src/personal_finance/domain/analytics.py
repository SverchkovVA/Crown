"""Доходы и расходы по принятым проводкам; без записи, пересчёта валют и float."""

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date

from personal_finance.domain.errors import ValidationError
from personal_finance.domain.ledger import LedgerRole, Operation, require_identifier
from personal_finance.domain.models import Currency


@dataclass(frozen=True, slots=True)
class ReportPeriod:
    start: date
    end: date

    def __post_init__(self) -> None:
        if type(self.start) is not date or type(self.end) is not date or self.start > self.end:
            raise ValidationError("Укажите даты отчёта: начало не позже окончания.")


@dataclass(frozen=True, slots=True)
class FlowTotals:
    currency: Currency
    income_minor: int
    expense_minor: int

    @property
    def difference_minor(self) -> int:
        return self.income_minor - self.expense_minor


@dataclass(frozen=True, slots=True)
class CategoryFlow:
    category_id: str
    totals: FlowTotals


@dataclass(frozen=True, slots=True)
class MonthlyFlow:
    month: date
    totals: FlowTotals


@dataclass(frozen=True, slots=True)
class IncomeExpenseReport:
    period: ReportPeriod
    totals: tuple[FlowTotals, ...]
    categories: tuple[CategoryFlow, ...]
    months: tuple[MonthlyFlow, ...]
    operation_references: tuple[tuple[str, str], ...]

    @property
    def operation_count(self) -> int:
        return len(self.operation_references)


def income_expense_report(
    operations: Sequence[Operation], period: ReportPeriod
) -> IncomeExpenseReport:
    if not isinstance(period, ReportPeriod):
        raise ValidationError("Укажите период отчёта.")
    totals: dict[Currency, list[int]] = {}
    categories: dict[tuple[str, Currency], list[int]] = {}
    months: dict[tuple[date, Currency], list[int]] = {}
    currencies: dict[str, Currency] = {}
    seen: set[str] = set()
    references = []
    for operation in operations:
        if not isinstance(operation, Operation):
            raise ValidationError("Отчёт принимает только финансовые операции.")
        require_identifier(operation.id)
        require_identifier(operation.revision_id)
        if operation.id in seen:
            raise ValidationError("Передайте только текущую версию каждой операции без повторов.")
        seen.add(operation.id)
        value = operation.draft
        if operation.is_deleted or not period.start <= value.occurred_on <= period.end:
            continue
        postings = tuple(
            p for p in value.postings() if p.role in (LedgerRole.INCOME, LedgerRole.EXPENSE)
        )
        if not postings:
            continue
        currency = value.amount.currency
        if currency.code in currencies and currencies[currency.code] != currency:
            raise ValidationError("Определения одной валюты в отчёте различаются.")
        currencies[currency.code] = currency
        references.append((operation.id, operation.revision_id))
        month = value.occurred_on.replace(day=1)
        for posting in postings:
            if posting.reference_id is None:
                raise ValidationError("У дохода или расхода отсутствует категория.")
            index = 0 if posting.role == LedgerRole.INCOME else 1
            # Доходная проводка отрицательна; расход положителен, возврат отрицателен.
            amount = -posting.amount.minor if index == 0 else posting.amount.minor
            totals.setdefault(currency, [0, 0])[index] += amount
            categories.setdefault((posting.reference_id, currency), [0, 0])[index] += amount
            months.setdefault((month, currency), [0, 0])[index] += amount
    return IncomeExpenseReport(
        period,
        tuple(
            FlowTotals(currency, *totals[currency])
            for currency in sorted(totals, key=lambda c: c.code)
        ),
        tuple(
            CategoryFlow(identifier, FlowTotals(currency, *values))
            for (identifier, currency), values in sorted(
                categories.items(), key=lambda item: (item[0][1].code, item[0][0])
            )
        ),
        tuple(
            MonthlyFlow(month, FlowTotals(currency, *values))
            for (month, currency), values in sorted(
                months.items(), key=lambda item: (item[0][0], item[0][1].code)
            )
        ),
        tuple(sorted(references)),
    )
