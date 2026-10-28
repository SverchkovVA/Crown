"""Общий разбор по полям и геометрии, без логотипов и названий банков."""

import re
from dataclasses import dataclass, replace

from personal_finance.domain.errors import ValidationError
from personal_finance.domain.models import Money
from personal_finance.domain.ocr import OcrDocument
from personal_finance.domain.ocr_cards import (
    CashDirection,
    OcrCardDraft,
    OcrField,
    OcrHistoryDraft,
    OcrIssue,
)
from personal_finance.infrastructure.ocr.generic_dates import date_spans, parse_date
from personal_finance.infrastructure.ocr.rub_fields import parse_rub_fields

_NUMBER = r"(?:[0-9]{1,3}(?:[ \u00a0\u202f][0-9]{3})+|[0-9]+)(?:[,.][0-9]{1,2})?"
_CURRENCY = r"(?:RUB|USD|EUR|CNY|KZT|BYN|GBP|CHF|JPY|₽|руб\.?|[РP$€£¥])"
_AMOUNT = re.compile(
    rf"(?<![\w.,+\-−—–])(?P<money>(?:[+\-−—–]\s*)?{_NUMBER}\s*{_CURRENCY}|[+\-−—–]\s*{_NUMBER})(?![\w.,])",
    re.I,
)
_FOREIGN = re.compile(r"(?:\b(?:USD|EUR|CNY|KZT|BYN|GBP|CHF|JPY)\b|[$€£¥])", re.I)
_SUMMARY = re.compile(
    r"^(?:остаток|баланс|доступно|всего|итого|(?:входящий|исходящий)\s+остаток|доходы\s+за|"
    r"расходы\s+за|потрачено|получено\s+за|оборот|лимит)\b",
    re.I,
)
_DETAILS = re.compile(
    r"^(?:сч[её]т|номер|карта|телефон|мсс|mcc|инн|бик|идентификатор|id|авторизац|"
    r"договор|срок действия|период)\b",
    re.I,
)
_PENDING = re.compile(
    r"(?:\b(?:отмен[её]н[ао]?|отклон[её]н[ао]?|ожидает|ожидание|запланировано|"
    r"черновик|обрабатывается|исполняется)\b|в\s*обработке|не\s+выполнен[ао]?)",
    re.I,
)
_FEE = re.compile(r"^комиссия\b", re.I)
_HISTORY = re.compile(
    r"^(?:история(?:\s+операций|\s+платежей)?|операции|список операций|все операции|"
    r"платежи и переводы)$",
    re.I,
)
_HEADING = re.compile(
    r"^(?:детали операции|информация об операции|операция(?: выполнена)?|"
    r"перевод(?:\s+.*)?|покупка|оплата(?:\s+.*)?|плат[её]ж|поступление|пополнение|"
    r"зачисление|списание|чек)$",
    re.I,
)
_FIELD = re.compile(
    r"^(?:сумма(?: операции|платежа|перевода)?|дата(?: и время| операции| перевода|"
    r" проведения| платежа)?|получатель|отправитель|описание|назначение|магазин|"
    r"тип операции|статус)\s*:",
    re.I,
)
_MONEY_LABEL = re.compile(
    r"^(?:сумма(?: операции|платежа|перевода)?|списано|зачислено)\s*:?\s*", re.I
)
_DIRECTION_LABEL = re.compile(r"^(?P<label>списано|зачислено)\b", re.I)


@dataclass(frozen=True)
class Row:
    text: str
    refs: tuple[int, ...]
    top: int
    bottom: int
    height: int

    def words(self, document: OcrDocument, start: int, end: int) -> tuple[int, ...]:
        result = []
        position = 0
        for index in self.refs:
            stop = position + len(document.words[index].text)
            if position < end and stop > start:
                result.append(index)
            position = stop + 1
        return tuple(result)


def visual_rows(document: OcrDocument) -> list[Row]:
    groups: list[list[int]] = []
    for index in sorted(
        range(len(document.words)),
        key=lambda i: (
            document.words[i].box.top * 2 + document.words[i].box.height,
            document.words[i].box.left,
        ),
    ):
        box = document.words[index].box
        for group in reversed(groups[-3:]):
            anchor = document.words[group[0]].box
            overlap = min(anchor.top + anchor.height, box.top + box.height) - max(
                anchor.top, box.top
            )
            if overlap > 0 and 2 * overlap >= min(anchor.height, box.height):
                group.append(index)
                break
        else:
            groups.append([index])
    result = []
    for group in groups:
        refs = tuple(sorted(group, key=lambda i: (document.words[i].box.left, i)))
        top = min(document.words[i].box.top for i in refs)
        bottom = max(document.words[i].box.top + document.words[i].box.height for i in refs)
        result.append(
            Row(" ".join(document.words[i].text for i in refs), refs, top, bottom, bottom - top)
        )
    return sorted(result, key=lambda row: row.top)


def _dates(document: OcrDocument, row: Row) -> list[tuple[str, tuple[int, ...]]]:
    monies = _money(document, row)
    return [
        (text, row.words(document, start, end))
        for start, end, text in date_spans(row.text)
        if not any(start < money[3] and end > money[2] for money in monies)
    ]


def _money(document: OcrDocument, row: Row) -> list[tuple[str, tuple[int, ...], int, int]]:
    values = []
    for match in _AMOUNT.finditer(row.text):
        prefix = row.text[: match.start()]
        suffix = row.text[match.end() :]
        signed = bool(re.match(r"[+\-−—–]", match["money"].lstrip()))
        if not signed and re.search(r"(?:[0-9][0-9.,]*|[+\-−—–])\s*$", prefix):
            try:
                dated = parse_date(prefix)
            except ValueError:
                dated = None
            if dated is None:
                continue
        if re.match(r"\s+[0-9]", suffix) and not re.search(_CURRENCY + r"$", match["money"], re.I):
            try:
                dated = parse_date(suffix)
            except ValueError:
                dated = None
            if dated is None:
                continue
        refs = row.words(document, match.start(), match.end())
        if refs:
            values.append((match["money"], refs, match.start(), match.end()))
    # Подписанное поле суммы может не иметь ни знака, ни валюты.
    if not values:
        label = _MONEY_LABEL.match(row.text)
        if label:
            tail = row.text[label.end() :].strip()
            if re.fullmatch(_NUMBER, tail):
                values.append(
                    (
                        "Сумма: " + tail,
                        row.words(document, label.end(), len(row.text)),
                        label.end(),
                        len(row.text),
                    )
                )
    return values


def _date_fields(
    document: OcrDocument, values: list[tuple[str, tuple[int, ...]]]
) -> tuple[OcrField, OcrField]:
    missing = (OcrIssue.MISSING,)
    if not values:
        return OcrField(None, issues=missing), OcrField(None, issues=missing)
    if len(values) > 1:
        refs = tuple(dict.fromkeys(i for _, refs in values for i in refs))
        return OcrField(None, refs, (OcrIssue.AMBIGUOUS,)), OcrField(
            None, refs, (OcrIssue.AMBIGUOUS,)
        )
    text, refs = values[0]
    low = (OcrIssue.LOW_CONFIDENCE,) if any(document.words[i].confidence < 80 for i in refs) else ()
    try:
        parts = parse_date(text)
        if parts is None:
            raise ValueError
        day = parts.calendar()
        return (
            OcrField(day, refs, low if day else (*low, OcrIssue.MISSING)),
            OcrField(
                parts.local_time,
                refs,
                (*low, OcrIssue.INVALID)
                if parts.invalid_time
                else low
                if parts.local_time
                else (*low, OcrIssue.MISSING),
            ),
        )
    except ValueError:
        return OcrField(None, refs, (*low, OcrIssue.INVALID)), OcrField(
            None, refs, (*low, OcrIssue.INVALID)
        )


def _draft(
    document: OcrDocument,
    money: tuple[str, tuple[int, ...], int, int],
    dates: list[tuple[str, tuple[int, ...]]],
    description: str,
    description_refs: tuple[int, ...],
    adapter: str,
    blocked: str = "",
    direction: tuple[CashDirection, tuple[int, ...]] | None = None,
) -> OcrCardDraft:
    amount, refs, _, _ = money
    value = parse_rub_fields(document, [], adapter_id=adapter)
    match = re.fullmatch(
        rf"(?:Сумма\s*:\s*)?(?P<sign>[+\-−—–]?)\s*(?P<number>{_NUMBER})"
        rf"\s*(?P<currency>{_CURRENCY})?",
        amount,
        re.I,
    )
    low = (OcrIssue.LOW_CONFIDENCE,) if any(document.words[i].confidence < 80 for i in refs) else ()
    if match is not None:
        try:
            minor = Money.parse(match["number"]).minor
            if minor <= 0:
                raise ValidationError("Нулевая сумма")
            amount_field = OcrField(minor, refs, low)
        except ValidationError:
            amount_field = OcrField(None, refs, (*low, OcrIssue.INVALID))
        code = match["currency"] or ""
        codes = {
            "rub": "RUB",
            "₽": "RUB",
            "руб": "RUB",
            "руб.": "RUB",
            "$": "USD",
            "€": "EUR",
            "£": "GBP",
            "¥": "JPY",
        }
        currency = codes.get(code.casefold(), code.upper() if len(code) == 3 else None)
        flow = (
            CashDirection.IN
            if match["sign"] == "+"
            else CashDirection.OUT
            if match["sign"] in ("-", "−")
            else None
        )
        value = replace(
            value,
            amount=amount_field,
            currency=OcrField(currency, refs, low if currency else (*low, OcrIssue.UNRECOGNIZED)),
            direction=OcrField(flow, refs, low if flow else (*low, OcrIssue.UNRECOGNIZED)),
        )
    occurred, clock = _date_fields(document, dates)
    if direction is not None:
        if value.direction.value is not None and value.direction.value != direction[0]:
            blocked = blocked or "Подпись и знак суммы задают разные направления."
        elif value.direction.value is None:
            value = replace(value, direction=OcrField(direction[0], direction[1]))
    if any(
        document.words[i].box.top + document.words[i].box.height >= document.height - 8
        for i in refs
    ):
        blocked = blocked or "Строка касается края изображения. Нужен полный снимок."
    return replace(
        value,
        occurred_on=occurred,
        local_time=clock,
        description=OcrField(
            description[:500] or None, description_refs, () if description else (OcrIssue.MISSING,)
        ),
        blocked_reason=value.blocked_reason or blocked,
    )


def _residue(row: Row, money: tuple[str, tuple[int, ...], int, int]) -> str:
    _, _, start, end = money
    text = row.text[:start] + " " + row.text[end:]
    for a, b, _ in reversed(date_spans(text)):
        text = text[:a] + " " + text[b:]
    if re.fullmatch(
        r"(?:сегодня|вчера|пн|вт|ср|чт|пт|сб|вс|понедельник|вторник|среда|"
        r"четверг|пятница|суббота|воскресенье)[, .]*",
        text.strip(),
        re.I,
    ):
        return ""
    return re.sub(r"^(?:сегодня|вчера),?\s*", "", text.strip(), flags=re.I).strip(" ,·:—−-")


def _currency_hint(row: Row) -> tuple[str, tuple[int, ...]] | None:
    match = re.fullmatch(
        r"(?:валюта(?: операции)?\s*:?\s*)?(RUB|USD|EUR|CNY|KZT|BYN|GBP|CHF|JPY|₽|руб\.?|[$€£¥])",
        row.text,
        re.I,
    )
    if not match:
        return None
    code = match[1]
    mapping = {
        "₽": "RUB",
        "руб": "RUB",
        "руб.": "RUB",
        "$": "USD",
        "€": "EUR",
        "£": "GBP",
        "¥": "JPY",
    }
    return mapping.get(code.casefold(), code.upper()), row.refs


def _with_currency(card: OcrCardDraft, hint: tuple[str, tuple[int, ...]] | None) -> OcrCardDraft:
    if hint is None:
        return card
    code, refs = hint
    blocked = card.blocked_reason
    if code != "RUB":
        blocked = blocked or "Известная другая валюта: финансовый OCR пока поддерживает RUB."
    if card.currency.value is not None and card.currency.value != code:
        return replace(card, blocked_reason=blocked or "В источнике противоречивые валюты.")
    return replace(
        card,
        currency=OcrField(code, tuple(dict.fromkeys((*card.currency.word_indices, *refs)))),
        blocked_reason=blocked,
    )


class GenericCardAdapter:
    def parse(self, document: OcrDocument) -> OcrCardDraft:
        rows = visual_rows(document)
        candidates = []
        labelled = []
        hints = []
        dates = []
        description = ""
        description_refs: tuple[int, ...] = ()
        blocked = ""
        direction = None
        for index, row in enumerate(rows):
            monies = _money(document, row)
            bound_amount = False
            if not monies and index and re.fullmatch(_NUMBER, row.text):
                previous = rows[index - 1]
                label_only = re.fullmatch(
                    r"(?:сумма(?: операции|платежа|перевода)?|списано|зачислено)\s*:?",
                    previous.text,
                    re.I,
                )
                if label_only and 0 <= row.top - previous.bottom <= 3 * max(
                    row.height, previous.height
                ):
                    monies = [("Сумма: " + row.text, (*previous.refs, *row.refs), 0, len(row.text))]
                    bound_amount = True
            hint = _currency_hint(row)
            if hint is not None:
                hints.append(hint)
            if _MONEY_LABEL.match(row.text) and len(monies) == 1 or bound_amount:
                labelled.extend(monies)
            if _PENDING.search(row.text):
                blocked = "Непроведённая или отменённая операция не принимается."
            if _FEE.match(row.text):
                blocked = "Отдельная комиссия требует ручного финансового оформления."
            if _FOREIGN.search(row.text) and _money(document, row):
                blocked = "Известная другая валюта: финансовый OCR пока поддерживает RUB."
            if not bound_amount and not _DETAILS.match(row.text) and not _SUMMARY.match(row.text):
                dates.extend(_dates(document, row))
            if re.match(
                r"^(?:описание|назначение|получатель|отправитель|магазин)\s*:", row.text, re.I
            ):
                description = row.text.split(":", 1)[1].strip()
                description_refs = row.refs
            if (
                _SUMMARY.match(row.text)
                or _DETAILS.match(row.text)
                or re.match(r"^(?:к[еэ]шб[еэ]к|бонусы)\b", row.text, re.I)
            ):
                continue
            candidates.extend(monies)
            label = _DIRECTION_LABEL.match(row.text)
            if label:
                direction = (
                    CashDirection.OUT
                    if label["label"].casefold() == "списано"
                    else CashDirection.IN,
                    row.refs,
                )
        # Одна подписанная сумма и одно равное повторное отображение в карточке.
        if len(candidates) == 2 and len(labelled) == 1:
            checks = [_draft(document, m, [], "", (), "generic-card-v1") for m in candidates]
            if (
                checks[0].amount.value is not None
                and checks[0].amount.value == checks[1].amount.value
                and checks[0].currency.value == checks[1].currency.value
                and len({c.direction.value for c in checks if c.direction.value is not None}) <= 1
            ):
                repeated = next(m for m in candidates if m != labelled[0])
                repeated_row = next(
                    (r for r in rows if r.words(document, 0, len(r.text)) == repeated[1]), None
                )
                if repeated_row is not None and not _residue(repeated_row, repeated):
                    origin = tuple(dict.fromkeys(i for money in candidates for i in money[1]))
                    primary = labelled[0]
                    candidates = [(primary[0], origin, primary[2], primary[3])]
                    if direction is None:
                        direction = next(
                            (
                                (c.direction.value, c.direction.word_indices)
                                for c in checks
                                if c.direction.value is not None
                            ),
                            None,
                        )
        context = any(_HEADING.fullmatch(row.text) or _FIELD.match(row.text) for row in rows)
        if any(_HISTORY.fullmatch(row.text) for row in rows):
            blocked = "Это список операций: выберите распознавание истории."
        if not context:
            blocked = (
                blocked or "Не выделены поля одной операции. Выберите список или уточните источник."
            )
        if len(candidates) != 1:
            blocked = blocked or (
                "Найдено несколько сумм. Выберите историю операций."
                if candidates
                else "Не выделена область основной суммы."
            )
        if len(candidates) == 1:
            result = _draft(
                document,
                candidates[0],
                dates,
                description,
                description_refs,
                "generic-card-v1",
                blocked,
                direction,
            )
            for hint in hints:
                result = _with_currency(result, hint)
            return result
        empty = parse_rub_fields(document, [], adapter_id="generic-card-v1")
        return replace(empty, blocked_reason=blocked)


class GenericHistoryAdapter:
    @staticmethod
    def has_heading(document: OcrDocument) -> bool:
        return any(_HISTORY.fullmatch(row.text) for row in visual_rows(document))

    def parse(self, document: OcrDocument) -> OcrHistoryDraft:
        rows = visual_rows(document)
        if not self.has_heading(document) and any(
            _FIELD.match(row.text)
            or row.text.casefold() in ("детали операции", "информация об операции")
            for row in rows
        ):
            return OcrHistoryDraft(
                document, (), 0, "Выделены поля карточки. Выберите одну операцию, а не историю."
            )
        active_date = None
        cards: list[OcrCardDraft] = []
        fragments = 0
        date_count = 0
        planned = False
        unbound_fee = False
        for index, row in enumerate(rows):
            dates = _dates(document, row)
            monies = _money(document, row)
            if row.text.casefold().strip() in ("запланировано", "ожидают", "черновики"):
                planned = True
                active_date = None
                continue
            if row.text.casefold().strip() in ("исполненные", "выполненные", "проведённые"):
                planned = False
                active_date = None
                continue
            if _FEE.match(row.text):
                fragments += len(monies)
                if cards:
                    cards[-1] = replace(
                        cards[-1],
                        blocked_reason="Рядом отдельная комиссия: требуется ручное оформление.",
                    )
                else:
                    unbound_fee = True
                continue
            if _SUMMARY.match(row.text) or _DETAILS.match(row.text):
                fragments += len(monies)
                continue
            if dates and not monies:
                active_date = dates[0] if len(dates) == 1 else None
                date_count += 1
                continue
            if dates and len(monies) == 1 and not _residue(row, monies[0]):
                active_date = dates[0] if len(dates) == 1 else None
                date_count += 1
                fragments += 1
                continue
            if not monies:
                if row.text.casefold().strip() in ("сегодня", "вчера") or re.match(
                    r"^[0-9]{1,2}(?:[./]|\s+[a-zа-яё]{3})", row.text, re.I
                ):
                    active_date = None
                continue
            if len(monies) != 1:
                fragments += len(monies)
                continue
            if dates:
                active_date = None
            money = monies[0]
            description = _residue(row, money)
            prefix = row.text[: money[2]].strip()
            if re.match(r"^(?:к[еэ]шб[еэ]к|бонусы)\b", prefix, re.I) and not re.match(
                r"[+\-−—–]", money[0]
            ):
                fragments += 1
                continue
            description_refs = row.words(document, 0, money[2]) + row.words(
                document, money[3], len(row.text)
            )
            # Описание может быть слева на предыдущей строке того же блока.
            if not description and index:
                previous = rows[index - 1]
                if 0 <= row.top - previous.bottom <= 2 * max(row.height, previous.height) and not (
                    _dates(document, previous)
                    or _money(document, previous)
                    or _SUMMARY.match(previous.text)
                    or _DETAILS.match(previous.text)
                    or _HEADING.fullmatch(previous.text)
                    or _HISTORY.fullmatch(previous.text)
                ):
                    description = previous.text
                    description_refs = previous.refs
            chosen = dates if dates else [active_date] if active_date else []
            blocked = ""
            if planned or _PENDING.search(row.text):
                blocked = "Непроведённая операция не принимается."
            if _FOREIGN.search(row.text):
                blocked = "Известная другая валюта: пока поддерживается RUB."
            if index:
                previous = rows[index - 1]
                if (
                    0 <= row.top - previous.bottom <= 2 * max(row.height, previous.height)
                    and not _money(document, previous)
                    and _PENDING.search(previous.text)
                ):
                    blocked = "Непроведённая операция не принимается."
            if re.match(r"^возврат(?: покупки| оплаты| платежа)?\b", prefix, re.I):
                blocked = "Возврат требует связи с покупкой в отдельной финансовой форме."
            if index + 1 < len(rows):
                following = rows[index + 1]
                if (
                    0 <= following.top - row.bottom <= 2 * max(row.height, following.height)
                    and not _money(document, following)
                    and _PENDING.search(following.text)
                ):
                    blocked = "Непроведённая операция не принимается."
            card = _draft(
                document,
                money,
                chosen,
                description,
                tuple(dict.fromkeys(description_refs)),
                "generic-history-v1",
                blocked,
            )
            if index + 1 < len(rows):
                following = rows[index + 1]
                if 0 <= following.top - row.bottom <= 2 * max(row.height, following.height):
                    card = _with_currency(card, _currency_hint(following))
            cards.append(card)
            if len(cards) > 100:
                return OcrHistoryDraft(
                    document, (), fragments, "На изображении более 100 денежных строк."
                )
        context = (
            any(_HISTORY.fullmatch(row.text) for row in rows)
            or date_count > 0
            or any(_dates(document, row) for row in rows)
        )
        blocked_history = (
            ""
            if cards and context
            else "Не выделен список операций с датами или заголовком истории."
        )
        if unbound_fee:
            blocked_history = (
                "Не удалось привязать комиссию к одной операции. Нужен полный источник."
            )
        money_rows = [
            r
            for r in rows
            if _money(document, r) and not _SUMMARY.match(r.text) and not _DETAILS.match(r.text)
        ]
        if any(
            _PENDING.search(r.text)
            and not any(abs(r.top - m.top) <= 3 * max(r.height, m.height) for m in money_rows)
            for r in rows
        ):
            blocked_history = (
                "Не удалось привязать непроведённый статус к строке. Нужен полный источник."
            )
        return OcrHistoryDraft(document, tuple(cards), fragments, blocked_history)
