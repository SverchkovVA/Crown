"""Опытная карточка ВТБ «Детали операции» с подписанной датой."""

import re
from dataclasses import replace

from personal_finance.domain.ocr import OcrDocument
from personal_finance.domain.ocr_cards import OcrCardDraft, OcrField, OcrIssue
from personal_finance.infrastructure.ocr.rows import document_rows
from personal_finance.infrastructure.ocr.rub_fields import _DATE, _MONEY, _NUMBER, parse_rub_fields


class VtbCardAdapter:
    def parse(self, document: OcrDocument) -> OcrCardDraft:
        rows = sorted(
            document_rows(document), key=lambda row: min(document.words[i].box.top for i in row[1])
        )
        selected = []
        consumed: set[int] = set()
        labelled_amounts = []
        for index, (text, evidence) in enumerate(rows):
            if index in consumed:
                continue
            label, separator, value = text.partition(":")
            name = label.casefold().strip()
            date_prefix = re.match(
                r"^(дата и время(?: операции)?|дата операции|дата перевода|"
                r"дата проведения|дата)\s*:?\s*(.*)$",
                text,
                re.I,
            )
            if date_prefix is not None:
                name = "дата и время"
                value = date_prefix[2].strip()
                separator = ":" if value else ""
            if name in ("дата и время", "дата операции", "сумма", "сумма операции"):
                bound = None
                if separator and value.strip():
                    bound = (value.strip(), evidence)
                elif index + 1 < len(rows):
                    next_text, next_evidence = rows[index + 1]
                    boxes = [document.words[i].box for i in (*evidence, *next_evidence)]
                    gap = min(document.words[i].box.top for i in next_evidence) - max(
                        document.words[i].box.top + document.words[i].box.height for i in evidence
                    )
                    expected = _MONEY if name.startswith("сумма") else _DATE
                    if 0 <= gap <= max(b.height for b in boxes) * 3 and expected.fullmatch(
                        next_text
                    ):
                        bound = (next_text, (*evidence, *next_evidence))
                        consumed.add(index + 1)
                if bound is not None:
                    value, origin = bound
                    if name.startswith("сумма") and _MONEY.fullmatch(value):
                        value = "Сумма: " + value
                        labelled_amounts.append((value, origin))
                    selected.append((value, origin))
            elif not _DATE.fullmatch(text):
                inline = _inline_amount(document, text, evidence)
                selected.extend(inline if inline is not None else [(text, evidence)])
        result = parse_rub_fields(document, selected, adapter_id="vtb-card-v1")
        if len(labelled_amounts) == 1:
            result = _repeated_summary(document, selected, labelled_amounts[0], result)
        headings = sum(text.casefold() == "детали операции" for text, _ in rows)
        if headings != 1 or any(text.casefold().startswith("история") for text, _ in rows):
            return replace(
                result,
                blocked_reason="Макет не поддержан: выберите одну карточку ВТБ «Детали операции».",
            )
        if any(text.casefold().split(":", 1)[0] == "комиссия" for text, _ in rows):
            return replace(
                result, blocked_reason="Карточка с отдельной комиссией пока не поддержана."
            )
        return result


def _repeated_summary(
    document: OcrDocument,
    rows: list[tuple[str, tuple[int, ...]]],
    labelled: tuple[str, tuple[int, ...]],
    original: OcrCardDraft,
) -> OcrCardDraft:
    """Только одна подписанная сумма и одно совпадающее отображение в её карточке."""
    monetary = []
    other = []
    for row in rows:
        match = _MONEY.fullmatch(row[0])
        if match and (match["currency"] or match["sign"] or row[0].casefold().startswith("сумма:")):
            monetary.append((row, match))
        else:
            other.append(row)
    if len(monetary) != 2:
        return original
    candidates = [
        parse_rub_fields(document, [row], adapter_id="vtb-card-v1") for row, _ in monetary
    ]
    first, second = candidates
    if first.amount.value is None or first.amount.value != second.amount.value:
        return original
    if first.currency.value != second.currency.value:
        return original
    if (
        first.currency.value is None
        and (monetary[0][1]["currency"] or "").casefold()
        != (monetary[1][1]["currency"] or "").casefold()
        and {
            (monetary[0][1]["currency"] or "").casefold(),
            (monetary[1][1]["currency"] or "").casefold(),
        }
        != {"р", "p"}
    ):
        return original
    directions = {c.direction.value for c in candidates if c.direction.value is not None}
    if len(directions) > 1:
        return original
    single = parse_rub_fields(document, [*other, labelled], adapter_id="vtb-card-v1")
    evidence = tuple(dict.fromkeys(i for (row, _) in monetary for i in row[1]))
    issues = (
        (OcrIssue.LOW_CONFIDENCE,)
        if any(document.words[i].confidence < 80 for i in evidence)
        else ()
    )
    flow = next(
        (c.direction for c in candidates if c.direction.value is not None), single.direction
    )
    return replace(
        single,
        amount=OcrField(single.amount.value, evidence, issues),
        currency=OcrField(single.currency.value, evidence, (*single.currency.issues, *issues)),
        direction=flow,
    )


_CARD_MONEY_TAIL = re.compile(
    rf"(?<![0-9A-Za-zА-Яа-я+\-−—–.,])"
    rf"(?P<money>[+\-−—–]?\s*{_NUMBER}\s*(?:RUB|₽|руб\.?|[РP])|"
    rf"[+\-−—–]\s*{_NUMBER})$",
    re.I,
)


def _inline_amount(
    document: OcrDocument, text: str, evidence: tuple[int, ...]
) -> list[tuple[str, tuple[int, ...]]] | None:
    """Разделить текст и единственную сумму справа, не обрезая плохую группировку."""
    if _MONEY.fullmatch(text):
        return None
    match = _CARD_MONEY_TAIL.search(text)
    if match is None:
        return None
    prefix = text[: match.start()].strip()
    parsed = _MONEY.fullmatch(match["money"])
    if re.match(r"(?:комиссия|сч[её]т|карта|номер|телефон|id)\b", prefix, re.I):
        return None
    if (
        not prefix
        or parsed is None
        or (not parsed["currency"] and not re.search(r"[,.][0-9]{1,2}$", parsed["amount"]))
    ):
        return None
    if re.search(r"(?:^|\s)[+\-−—–]?[0-9][0-9,.]*$|[+\-−—–]$", prefix) or re.search(
        r"[+\-−—–]\s*[0-9]|[0-9]\s*(?:RUB|₽|руб\.?|[РP])(?:\s|$)", prefix, re.I
    ):
        return None
    before, money = [], []
    position = 0
    for index in evidence:
        end = position + len(document.words[index].text)
        if end > match.start():
            money.append(index)
        else:
            before.append(index)
        position = end + 1
    if not money or min(document.words[i].box.left for i in money) < document.width // 2:
        return None
    return [(prefix, tuple(before)), (match["money"], tuple(money))]
