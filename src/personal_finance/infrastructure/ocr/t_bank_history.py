"""Опытный тип B: денежные строки справа под явными заголовками дат."""

import re
from dataclasses import replace

from personal_finance.domain.ocr import OcrDocument
from personal_finance.domain.ocr_cards import OcrField, OcrHistoryDraft, OcrIssue
from personal_finance.infrastructure.ocr.rub_fields import _DATE, _DATE_HINT, _NUMBER
from personal_finance.infrastructure.ocr.t_bank import TBankCardAdapter

_CURRENCY = r"(?:RUB|₽|руб\.?|[РP])"
_AMOUNT = re.compile(
    rf"(?<![0-9A-Za-zА-Яа-я+\-−—–.,])"
    rf"(?P<value>[+\-−—–]?\s*{_NUMBER}\s*{_CURRENCY}|[+\-−—–]\s*{_NUMBER}(?=\s*$))"
    r"(?![0-9A-Za-zА-Яа-я.,])",
    re.I,
)


class TBankHistoryAdapter:
    def parse(self, document: OcrDocument) -> OcrHistoryDraft:
        lookup = {(w.line_key, w.word_number): i for i, w in enumerate(document.words)}
        lines = sorted(document.lines(), key=lambda words: min(w.box.top for w in words))
        header: tuple[str, tuple[int, ...]] | None = None
        cards = []
        fragments = 0
        header_count = 0
        pending_status = False
        fee_row = False
        for words in lines:
            text = " ".join(w.text for w in words).strip()
            status = re.fullmatch(
                r"(?:(?:операция|перевод)\s+)?"
                r"(?:отмен[её]н[ао]?|отклон[её]н[ао]?|в обработке|обрабатывается)",
                text,
                re.I,
            )
            pending_status = pending_status or bool(status)
            indices = tuple(lookup[(w.line_key, w.word_number)] for w in words)
            if _DATE.fullmatch(text) or text.casefold() in ("сегодня", "вчера"):
                header = (text, indices)
                header_count += 1
                continue
            if _DATE_HINT.match(text):
                header = None
            offsets = []
            position = 0
            for word, index in zip(words, indices, strict=True):
                offsets.append((position, position + len(word.text), index))
                position += len(word.text) + 1
            matches = []
            for match in _AMOUNT.finditer(text):
                prefix = text[: match.start()].rstrip().rsplit(" ", 1)[-1]
                if re.fullmatch(r"[+\-−—–]?[0-9][0-9,.]*|[+\-−—–]", prefix):
                    fragments += 1
                    continue
                evidence = tuple(
                    i for left, right, i in offsets if left < match.end() and right > match.start()
                )
                if (
                    evidence
                    and min(document.words[i].box.left for i in evidence)
                    >= document.width * 55 // 100
                ):
                    matches.append((match, evidence))
            if not matches:
                continue
            if header is None:
                fragments += len(matches)
                continue
            if len(matches) != 1:
                fragments += len(matches)
                continue
            match, evidence = matches[0]
            rows = [header, (match["value"].strip(), evidence)]
            card = TBankCardAdapter().parse_rows(document, rows, require_heading=False)
            if header[0].casefold() in ("сегодня", "вчера"):
                card = replace(
                    card, occurred_on=OcrField(None, header[1], (OcrIssue.UNRECOGNIZED,))
                )
            prefix = text[: match.start()].strip()
            pending_status = pending_status or bool(
                re.fullmatch(r"(?:в обработке|обрабатывается|перевод отмен[её]н)", prefix, re.I)
            )
            fee_row = fee_row or bool(re.match(r"комиссия\b", prefix, re.I))
            description_indices = tuple(i for _, right, i in offsets if right <= match.start())
            if prefix and len(prefix) <= 500:
                issues = (
                    (OcrIssue.LOW_CONFIDENCE,)
                    if any(document.words[i].confidence < 80 for i in description_indices)
                    else ()
                )
                card = replace(card, description=OcrField(prefix, description_indices, issues))
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
        blocked = (
            ""
            if header_count and cards
            else "Макет истории не поддержан: нужны заголовок даты и денежная строка справа."
        )
        if pending_status:
            blocked = "В истории есть непроведённый статус. Такой снимок пока не принимается."
        if fee_row:
            blocked = "В истории есть отдельная комиссия. Такой снимок пока не принимается."
        return OcrHistoryDraft(document, tuple(cards), fragments, blocked)
