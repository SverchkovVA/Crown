"""Опытная история ВТБ: денежные строки справа под заголовком даты."""

import re
from dataclasses import replace

from personal_finance.domain.ocr import OcrDocument
from personal_finance.domain.ocr_cards import OcrField, OcrHistoryDraft, OcrIssue
from personal_finance.infrastructure.ocr.rub_fields import (
    _DATE,
    _DATE_HINT,
    _NUMBER,
    parse_rub_fields,
)

_WEEKDAY = re.compile(
    r"^(?:пн|вт|ср|чт|пт|сб|вс|понедельник|вторник|среда|четверг|пятница|суббота|воскресенье|сегодня|вчера),?\s+",
    re.I,
)
_CURRENCY = r"(?:RUB|₽|руб\.?|[РP])"
_TAIL = re.compile(
    rf"(?<![0-9A-Za-zА-Яа-я+\-−—–.,])"
    rf"(?P<money>(?:[+\-−—–]\s*{_NUMBER}\s*{_CURRENCY}?|{_NUMBER}\s*{_CURRENCY}))$",
    re.I,
)


class VtbHistoryAdapter:
    def parse(self, document: OcrDocument) -> OcrHistoryDraft:
        indices = {(w.line_key, w.word_number): i for i, w in enumerate(document.words)}
        lines = sorted(document.lines(), key=lambda line: min(w.box.top for w in line))
        header = None
        cards = []
        fragments = 0
        texts = []
        for line in lines:
            text = " ".join(w.text for w in line).strip()
            texts.append(text)
            standalone_date = _WEEKDAY.sub("", text)
            if _DATE.fullmatch(standalone_date) or standalone_date.casefold() in (
                "сегодня",
                "вчера",
            ):
                header = (
                    standalone_date,
                    tuple(indices[(w.line_key, w.word_number)] for w in line),
                )
                continue
            if _DATE_HINT.match(standalone_date):
                header = None
            match = _TAIL.search(text)
            if not match:
                continue
            prefix = text[: match.start()].strip()
            date_text = _WEEKDAY.sub("", prefix)
            money_indices = []
            date_indices = []
            position = 0
            for word in line:
                end = position + len(word.text)
                index = indices[(word.line_key, word.word_number)]
                if end > match.start():
                    money_indices.append(index)
                else:
                    date_indices.append(index)
                position = end + 1
            evidence = tuple(money_indices)
            if not evidence:
                continue
            if _DATE.fullmatch(date_text) or date_text.casefold() in ("сегодня", "вчера"):
                # Сумма рядом с заголовком даты может быть итогом дня. Не операция.
                header = (date_text, tuple(date_indices))
                fragments += 1
                continue
            if re.search(r"(?:^|\s)[+\-−—–]?[0-9][0-9,.]*$|[+\-−—–]$", prefix):
                fragments += 1
                continue
            if (
                header is None
                or min(document.words[i].box.left for i in evidence) < document.width * 55 // 100
            ):
                fragments += 1
                continue
            if re.search(r"[+\-−—–]\s*[0-9]|[0-9]\s*(?:RUB|₽|руб\.?|[РP])(?:\s|$)", prefix, re.I):
                fragments += 1
                continue
            card = parse_rub_fields(
                document, [header, (match["money"], evidence)], adapter_id="vtb-history-v1"
            )
            if prefix:
                description = tuple(date_indices)
                issues = (
                    (OcrIssue.LOW_CONFIDENCE,)
                    if any(document.words[i].confidence < 80 for i in description)
                    else ()
                )
                if len(prefix) <= 500:
                    card = replace(card, description=OcrField(prefix, description, issues))
            if any(
                document.words[i].box.top + document.words[i].box.height >= document.height - 8
                for i in evidence
            ):
                card = replace(
                    card,
                    blocked_reason="Строка касается края изображения. Проверьте полный источник.",
                )
            cards.append(card)
            if len(cards) > 100:
                return OcrHistoryDraft(
                    document, (), fragments, "На изображении более 100 денежных строк."
                )
        blocked = ""
        if not any(t.casefold().split(" ", 1)[0] == "история" for t in texts) or not cards:
            blocked = "Макет истории ВТБ не поддержан: нужны заголовок, дата слева и сумма справа."
        if any(
            re.fullmatch(
                r"(?:статус:\s*)?(?:(?:операция|перевод)\s+)?"
                r"(?:отмен[её]н[ао]?|отклон[её]н[ао]?|в обработке|обрабатывается)",
                t,
                re.I,
            )
            for t in texts
        ):
            blocked = "В истории есть непроведённый статус. Такой снимок пока не принимается."
        if any(re.match(r"комиссия(?:\s*:|$)", t, re.I) for t in texts):
            blocked = "В истории есть отдельная комиссия. Такой снимок пока не принимается."
        return OcrHistoryDraft(document, tuple(cards), fragments, blocked)
