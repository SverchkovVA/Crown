"""Сравнение сумм двух периодов одного снимка, без записи и пересчёта валют."""

from collections.abc import Sequence
from dataclasses import dataclass

from personal_finance.domain.analytics import (
    FlowTotals,
    IncomeExpenseReport,
    ReportPeriod,
    ReportScope,
    income_expense_report,
)
from personal_finance.domain.errors import ValidationError
from personal_finance.domain.ledger import Operation
from personal_finance.domain.models import Currency


@dataclass(frozen=True, slots=True)
class ComparisonRow:
    before: FlowTotals
    after: FlowTotals

    @property
    def income_delta(self) -> int:
        return self.after.income_minor - self.before.income_minor

    @property
    def expense_delta(self) -> int:
        return self.after.expense_minor - self.before.expense_minor

    @property
    def difference_delta(self) -> int:
        return self.after.difference_minor - self.before.difference_minor


@dataclass(frozen=True, slots=True)
class CategoryComparison:
    category_id: str
    row: ComparisonRow


@dataclass(frozen=True, slots=True)
class IncomeExpenseComparison:
    before: IncomeExpenseReport
    after: IncomeExpenseReport
    totals: tuple[ComparisonRow, ...]
    categories: tuple[CategoryComparison, ...]


def compare_periods(
    operations: Sequence[Operation],
    before_period: ReportPeriod,
    after_period: ReportPeriod,
    scope: ReportScope | None = None,
) -> IncomeExpenseComparison:
    snapshot = tuple(operations)
    before = income_expense_report(snapshot, before_period, scope)
    after = income_expense_report(snapshot, after_period, scope)
    currencies: dict[str, Currency] = {}
    for flow in (*before.totals, *after.totals):
        currency = flow.currency
        if currency.code in currencies and currencies[currency.code] != currency:
            raise ValidationError("Определения одной валюты в сравнении различаются.")
        currencies[currency.code] = currency

    def row(a: FlowTotals | None, b: FlowTotals | None, currency: Currency) -> ComparisonRow:
        return ComparisonRow(
            a if a is not None else FlowTotals(currency, 0, 0),
            b if b is not None else FlowTotals(currency, 0, 0),
        )

    first = {v.currency: v for v in before.totals}
    second = {v.currency: v for v in after.totals}
    totals = tuple(
        row(first.get(currency), second.get(currency), currency)
        for currency in sorted(currencies.values(), key=lambda v: v.code)
    )
    first_categories = {(v.category_id, v.totals.currency): v.totals for v in before.categories}
    second_categories = {(v.category_id, v.totals.currency): v.totals for v in after.categories}
    keys = first_categories.keys() | second_categories.keys()
    categories = tuple(
        CategoryComparison(
            key[0], row(first_categories.get(key), second_categories.get(key), key[1])
        )
        for key in sorted(keys, key=lambda v: (v[1].code, v[0]))
    )
    return IncomeExpenseComparison(before, after, totals, categories)
