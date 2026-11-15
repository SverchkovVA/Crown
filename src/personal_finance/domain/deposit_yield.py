"""Фактическая годовая доходность по явно связанным датированным движениям."""

from collections.abc import Sequence
from datetime import date
from decimal import Context, Decimal, localcontext

from personal_finance.domain.deposits import MAX_EVENTS, MAX_TERM_DAYS
from personal_finance.domain.errors import ValidationError
from personal_finance.domain.models import Currency, Money

_YEAR_DAYS = 365
_CALCULATION = Context(prec=72)
_SEARCH_STEPS = 192


def effective_deposit_yield(flows: Sequence[tuple[date, Money]]) -> Decimal:
    """Возвращает процент годовых для потоков вкладчика с одним переходом знака.

    Вклад и пополнения имеют отрицательный знак, выплаты и оценка
    оставшегося вклада на дату среза — положительный. В один день потоки
    объединяются. Учитываются фактические календарные дни и год из 365 дней.
    """
    if not 2 <= len(flows) <= MAX_EVENTS:
        raise ValidationError("Для доходности нужны пополнения и выплаты в пределах срока.")
    by_day: dict[date, int] = {}
    currency: Currency | None = None
    for day, money in flows:
        if type(day) is not date or not isinstance(money, Money):
            raise ValidationError("Укажите дату и точную сумму каждого движения.")
        if currency is None:
            currency = money.currency
        elif currency != money.currency:
            raise ValidationError("Все движения вклада должны быть в одной валюте.")
        by_day[day] = by_day.get(day, 0) + money.minor
    ordered = sorted((day, minor) for day, minor in by_day.items() if minor)
    if len(ordered) < 2 or ordered[0][1] >= 0 or ordered[-1][1] <= 0:
        raise ValidationError("Нужны внесение и выплата либо остаток на более позднюю дату.")
    first, last = ordered[0][0], ordered[-1][0]
    if (last - first).days > MAX_TERM_DAYS:
        raise ValidationError("Срок расчёта доходности превышает предел для сценария вклада.")

    saw_payout = False
    for _, minor in ordered:
        if minor > 0:
            saw_payout = True
        elif saw_payout:
            raise ValidationError("После выплаты есть новое пополнение. Доходность неоднозначна.")

    net = sum(minor for _, minor in ordered)
    if net == 0:
        return Decimal(0)
    gain = net > 0
    # При убытке обращаем даты. Тогда искомый множитель всегда в (0, 1),
    # а степени неотрицательны при обоих знаках результата.
    terms = tuple(
        ((day - first).days if gain else (last - day).days, Decimal(minor))
        for day, minor in ordered
    )
    with localcontext(_CALCULATION):
        low, high = Decimal(0), Decimal(1)
        for _ in range(_SEARCH_STEPS):
            step = (low + high) / 2
            value = sum(minor * step**offset for offset, minor in terms)
            if value == 0:
                low = high = step
                break
            if (value > 0) == gain:
                high = step
            else:
                low = step
        factor = (low + high) / 2
        annual = (Decimal(1) / factor if gain else factor) ** _YEAR_DAYS
        return (annual - 1) * 100
