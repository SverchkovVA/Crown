"""Ограниченная страница месячных остатков одного готового снимка."""

from calendar import monthrange
from dataclasses import dataclass
from datetime import date

from personal_finance.domain.analytics import ReportPeriod
from personal_finance.domain.analytics_cashflow import (
    CashFlowReport,
    CashMovement,
    CashPosition,
    CashSourceKind,
    cash_flow_sources,
)
from personal_finance.domain.errors import ValidationError
from personal_finance.domain.ledger import OperationKind
from personal_finance.domain.models import Currency


def month_index(day: date) -> int:
    return (day.year - 1) * 12 + day.month - 1


@dataclass(frozen=True, slots=True)
class CashMonth:
    period: ReportPeriod
    totals: CashPosition


@dataclass(frozen=True, slots=True)
class CashMonthPage:
    report: CashFlowReport
    currency: Currency
    account_id: str | None
    offset: int
    month_count: int
    rows: tuple[CashMonth, ...]
    lower_minor: int
    upper_minor: int


def cash_months(
    report: CashFlowReport,
    currency: Currency,
    *,
    account_id: str | None = None,
    offset: int = 0,
    limit: int = 6,
) -> CashMonthPage:
    if not isinstance(report, CashFlowReport):
        raise ValidationError("Передайте снимок отчёта движения средств.")
    first, last = month_index(report.period.start), month_index(report.period.end)
    count = last - first + 1
    if type(offset) is not int or not 0 <= offset < count:
        raise ValidationError("Начальный месяц вне периода отчёта.")
    if type(limit) is not int or not 1 <= limit <= 24:
        raise ValidationError("На странице допустимо от 1 до 24 месяцев.")
    lines = cash_flow_sources(report, currency, CashSourceKind.THROUGH_END, account_id=account_id)
    page_first, page_last = first + offset, min(last, first + offset + limit - 1)
    groups: dict[int, list[int]] = {}
    changes: dict[int, int] = {}
    period_opening = 0
    page_opening = 0
    for line in lines:
        day, amount = line.operation.draft.occurred_on, line.minor
        index = month_index(day)
        if day < report.period.start:
            period_opening += amount
            page_opening += amount
            continue
        changes[index] = changes.get(index, 0) + amount
        if index < page_first:
            page_opening += amount
        elif index <= page_last:
            group = groups.setdefault(index, [0, 0, 0])
            if line.operation.draft.kind == OperationKind.OPENING:
                group[0] += amount
            elif amount > 0:
                group[1] += amount
            else:
                group[2] -= amount
    # Масштаб всего интервала вычисляется по непустым месяцам, без создания
    # десятков тысяч пустых записей. Пустой месяц сохраняет предыдущий остаток.
    balance = period_opening
    lower = upper = balance
    for index in sorted(changes):
        balance += changes[index]
        lower, upper = min(lower, balance), max(upper, balance)
    rows = []
    balance = page_opening
    for index in range(page_first, page_last + 1):
        year, month = index // 12 + 1, index % 12 + 1
        start = max(date(year, month, 1), report.period.start)
        end = min(date(year, month, monthrange(year, month)[1]), report.period.end)
        capital, incoming, outgoing = groups.get(index, [0, 0, 0])
        totals = CashPosition(currency, balance, capital, incoming, outgoing)
        rows.append(CashMonth(ReportPeriod(start, end), totals))
        balance = totals.closing_minor
    return CashMonthPage(report, currency, account_id, offset, count, tuple(rows), lower, upper)


def cash_month_sources(
    page: CashMonthPage, row: int, kind: CashSourceKind
) -> tuple[CashMovement, ...]:
    if not isinstance(page, CashMonthPage) or type(row) is not int or not 0 <= row < len(page.rows):
        raise ValidationError("Выберите месяц на текущей странице.")
    if not isinstance(kind, CashSourceKind):
        raise ValidationError("Выберите часть месячного остатка.")
    period = page.rows[row].period
    return tuple(
        line
        for line in cash_flow_sources(
            page.report, page.currency, CashSourceKind.THROUGH_END, account_id=page.account_id
        )
        if (
            kind == CashSourceKind.BEFORE
            and line.operation.draft.occurred_on < period.start
            or kind == CashSourceKind.PERIOD
            and period.start <= line.operation.draft.occurred_on <= period.end
            or kind == CashSourceKind.THROUGH_END
            and line.operation.draft.occurred_on <= period.end
        )
    )
