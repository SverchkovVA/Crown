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

_SPACE = r"[ \u00a0\u202f]"
_NUMBER = (
    rf"(?:[0-9]{{1,3}}(?:{_SPACE}[0-9]{{3}})+|[0-9]+)"
    rf"(?:{_SPACE}*[,.]{_SPACE}*[0-9]{{1,2}})?"
)
_RUSSIAN_RUBLE = r"руб(?:лей|ля|ль|\.?)"
_CURRENCY = rf"(?:RUB|USD|EUR|CNY|KZT|BYN|GBP|CHF|JPY|₽|{_RUSSIAN_RUBLE}|[РP$€£¥])"
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
    r"переводы|перевод(?:\s+.*)?|покупка|оплата(?:\s+.*)?|плат[её]ж|поступление|пополнение|"
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
_ACCOUNT_SECTION = re.compile(
    r"^(?:покупка по карт[её]|оплата по карт[её]|перевод со сч[её]та|"
    r"списано со сч[её]та|зачислено на сч[её]т|"
    r"сч[её]т (?:списания|зачисления|отправителя|получателя)|"
    r"(?:с|со|на) сч[её]т(?:а)?|карта (?:списания|зачисления)|реквизиты|"
    r"номер (?:телефона|карты|сч[её]та))\b",
    re.I,
)
_MISSING_MAIN_AMOUNT = "Не выделена область основной суммы."


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


def _bounded_date_spans(text: str) -> list[tuple[int, int, str]]:
    found = date_spans(text)
    if len(found) >= 2:
        return found[:2]
    if found:
        # Две соседние даты должны дать неоднозначность, а не только последнюю.
        earlier = date_spans(text[: found[0][0]])
        if earlier:
            return [earlier[-1], found[0]]
    return found


def _ends_in_date(prefix: str) -> bool:
    prefix = prefix.rstrip()
    spans = date_spans(prefix)
    candidates = [prefix]
    if spans and spans[-1][1] == len(prefix):
        candidates.append(spans[-1][2])
    for text in candidates:
        try:
            if parse_date(text) is not None:
                return True
        except ValueError:
            pass
    return False


def _dates(document: OcrDocument, row: Row) -> list[tuple[str, tuple[int, ...]]]:
    monies = _money(document, row)
    gaps = []
    cursor = 0
    for _, _, start, end in monies:
        gaps.append((cursor, start))
        cursor = end
    gaps.append((cursor, len(row.text)))
    values = []
    for start, end in gaps:
        for left, right, text in _bounded_date_spans(row.text[start:end]):
            values.append((text, row.words(document, start + left, start + right)))
            if len(values) == 2:
                return values
    return values


def _money(document: OcrDocument, row: Row) -> list[tuple[str, tuple[int, ...], int, int]]:
    values = []
    for match in _AMOUNT.finditer(row.text):
        prefix = row.text[: match.start()]
        suffix = row.text[match.end() :]
        if re.match(r"\s*%", suffix):
            continue
        signed = bool(re.match(r"[+\-−—–]", match["money"].lstrip()))
        if (
            not signed
            and re.search(r"(?:[0-9][0-9., \u00a0\u202f]*|[+\-−—–])\s*$", prefix)
            and not _ends_in_date(prefix)
        ):
            continue
        if re.match(r"\s+[0-9]", suffix) and not re.search(_CURRENCY + r"$", match["money"], re.I):
            try:
                dated = parse_date(suffix)
            except ValueError:
                dated = None
            if dated is None:
                continue
        if not re.search(_CURRENCY + r"$", match["money"], re.I) and re.match(
            rf"{_SPACE}*[,.]", suffix
        ):
            # Нельзя принять целую часть, если дробная часть не прошла грамматику.
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
            number = match["number"].replace("\u00a0", " ").replace("\u202f", " ")
            minor = Money.parse(number).minor
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
            "рубль": "RUB",
            "рубля": "RUB",
            "рублей": "RUB",
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
        rf"(?:валюта(?: операции)?\s*:?\s*)?"
        rf"(RUB|USD|EUR|CNY|KZT|BYN|GBP|CHF|JPY|₽|{_RUSSIAN_RUBLE}|[$€£¥])",
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
        "рубль": "RUB",
        "рубля": "RUB",
        "рублей": "RUB",
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


def _noncash_quantity(document: OcrDocument, row: Row) -> bool:
    for token, _, _, end in _money(document, row):
        if re.search(_CURRENCY + r"$", token, re.I):
            continue
        if re.match(
            r"\s*[)\]]?\s*(?:балл(?:а|ов|ы)?|бонус(?:а|ов|ы)?|мил(?:я|и|ь))\b", row.text[end:], re.I
        ):
            return True
        if re.match(r"^(?:бонусы|баллы|мили)\b", row.text, re.I):
            return True
    return False


def history_subtitles(document: OcrDocument, rows: list[Row]) -> dict[int, int]:
    """Мелкая подпись под основной строкой: категория/бонус, а не новое движение."""
    result = {}
    for index in range(1, len(rows)):
        row, previous = rows[index], rows[index - 1]
        amounts, before = _money(document, row), _money(document, previous)
        if len(amounts) != 1 or len(before) != 1 or _dates(document, row):
            continue
        primary_boxes = [document.words[i].box for i in before[0][1]]
        primary_bottom = max(box.top + box.height for box in primary_boxes)
        primary_height = max(box.height for box in primary_boxes)
        bonus_height = max(document.words[i].box.height for i in amounts[0][1])
        left = min(document.words[i].box.left for i in amounts[0][1])
        earlier_left = min(document.words[i].box.left for i in before[0][1])
        if (
            0 <= row.top - primary_bottom <= 2 * max(bonus_height, primary_height)
            and 10 * bonus_height <= 9 * primary_height
            and (left + 2 * primary_height < earlier_left or _noncash_quantity(document, row))
            and row.text[: amounts[0][2]].strip()
            and _residue(previous, before[0])
        ):
            result[index] = index - 1
    return result


def card_reward_rows(document: OcrDocument, rows: list[Row]) -> set[int]:
    primary = [
        row
        for row in rows
        if len(_money(document, row)) == 1
        and not _residue(row, _money(document, row)[0])
        and re.match(r"[+\-−—–]", _money(document, row)[0][0])
    ]
    if not primary:
        return set()
    prominent = max(primary, key=lambda row: row.height)
    return {
        index
        for index, row in enumerate(rows)
        if row.top > prominent.bottom
        and row.height < prominent.height
        and re.match(r"^(?:к[еэ]шб[еэ]к|бонус(?:ы|ов)?|балл(?:ы|ов)?|мили)\b", row.text, re.I)
    }


def _dated_prominent_card(document: OcrDocument, rows: list[Row], candidates: list) -> bool:
    if len(candidates) != 1 or not any(_ACCOUNT_SECTION.match(row.text) for row in rows):
        return False
    refs = set(candidates[0][1])
    main = next((row for row in rows if refs.intersection(row.refs)), None)
    if main is None or _residue(main, candidates[0]):
        return False
    for row in rows:
        dates = _dates(document, row)
        if row.bottom >= main.top or row.height >= main.height or len(dates) != 1:
            continue
        try:
            value = parse_date(dates[0][0])
        except ValueError:
            continue
        if value is not None and value.local_time is not None:
            return True
    return False


def _contextual(card: OcrCardDraft) -> OcrCardDraft:
    from personal_finance.infrastructure.ocr.operation_context import with_operation_context

    return with_operation_context(card)


class GenericCardAdapter:
    @staticmethod
    def can_retry(card: OcrCardDraft) -> bool:
        return card.amount.value is None and card.blocked_reason == _MISSING_MAIN_AMOUNT

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
        secondary = False
        reward_rows = card_reward_rows(document, rows)
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
            if _ACCOUNT_SECTION.match(row.text):
                secondary = True
            label_only = re.fullmatch(
                r"(?:сумма(?: операции|платежа|перевода)?|списано|зачислено)\s*:?",
                row.text,
                re.I,
            )
            if not _ACCOUNT_SECTION.match(row.text) and (
                bound_amount or _MONEY_LABEL.match(row.text) and (monies or label_only)
            ):
                secondary = False
            if (_MONEY_LABEL.match(row.text) and len(monies) == 1) or bound_amount:
                labelled.extend(monies)
            if _PENDING.search(row.text):
                blocked = "Непроведённая или отменённая операция не принимается."
            if _FEE.match(row.text):
                blocked = "Отдельная комиссия требует ручного финансового оформления."
            if _FOREIGN.search(row.text) and _money(document, row):
                blocked = "Известная другая валюта: финансовый OCR пока поддерживает RUB."
            if (
                not secondary
                and not bound_amount
                and not _DETAILS.match(row.text)
                and not _SUMMARY.match(row.text)
            ):
                dates.extend(_dates(document, row))
            if re.match(
                r"^(?:описание|назначение|получатель|отправитель|магазин)\s*:", row.text, re.I
            ):
                description = row.text.split(":", 1)[1].strip()
                description_refs = row.refs
            if (
                secondary
                or index in reward_rows
                or _noncash_quantity(document, row)
                or _SUMMARY.match(row.text)
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
            checks = [_draft(document, m, [], "", (), "generic-card-v6") for m in candidates]
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
        context = context or _dated_prominent_card(document, rows, candidates)
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
                else _MISSING_MAIN_AMOUNT
            )
        if len(candidates) == 1:
            result = _draft(
                document,
                candidates[0],
                dates,
                description,
                description_refs,
                "generic-card-v6",
                blocked,
                direction,
            )
            for hint in hints:
                result = _with_currency(result, hint)
            return _contextual(result)
        empty = parse_rub_fields(document, [], adapter_id="generic-card-v6")
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
        subtitles = history_subtitles(document, rows)
        for index, row in enumerate(rows):
            if index in subtitles or _noncash_quantity(document, row):
                continue
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
                "generic-history-v5",
                blocked,
            )
            if index + 1 < len(rows):
                following = rows[index + 1]
                if 0 <= following.top - row.bottom <= 2 * max(row.height, following.height):
                    card = _with_currency(card, _currency_hint(following))
            cards.append(_contextual(card))
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
