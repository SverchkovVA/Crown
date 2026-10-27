"""Консервативный прототип карточки Т-Банка; банк выбирается явно."""

import re
from datetime import date, time

from personal_finance.domain.errors import ValidationError
from personal_finance.domain.models import Money
from personal_finance.domain.ocr import OcrDocument
from personal_finance.domain.ocr_cards import CashDirection, OcrCardDraft, OcrField, OcrIssue

_NUMBER = r"(?:[0-9]+|[0-9]{1,3}(?:[ \u00a0\u202f][0-9]{3})+)(?:[,.][0-9]{2})?"
_MONEY = re.compile(
    rf"(?:Сумма\s*:\s*)?(?P<sign>[+\-−—–]?)\s*(?P<amount>{_NUMBER})"
    r"\s*(?P<currency>RUB|₽|руб\.?|[РP])?",
    re.I,
)
_DATE = re.compile(
    r"(?:Дата\s*:\s*)?(?P<day>[0-9]{1,2})(?:\.(?P<month>[0-9]{2})\.(?P<year>[0-9]{4})"
    r"|\s+(?P<month_name>января|февраля|марта|апреля|мая|июня|июля|августа|сентября|"
    r"октября|ноября|декабря)(?:\s+(?P<text_year>[0-9]{4})(?:\s*г\.?)?)?)"
    r"(?:\s*(?:,|в)?\s*(?P<hour>[0-9]{1,2}):(?P<minute>[0-9]{2}))?",
    re.I,
)
_MONTHS = {
    name: i
    for i, name in enumerate(
        (
            "января",
            "февраля",
            "марта",
            "апреля",
            "мая",
            "июня",
            "июля",
            "августа",
            "сентября",
            "октября",
            "ноября",
            "декабря",
        ),
        1,
    )
}


class TBankCardAdapter:
    def parse(self, document: OcrDocument) -> OcrCardDraft:
        indices = {(w.line_key, w.word_number): i for i, w in enumerate(document.words)}
        rows = [
            (
                " ".join(w.text for w in line).strip(),
                tuple(indices[(w.line_key, w.word_number)] for w in line),
            )
            for line in document.lines()
        ]

        return self.parse_rows(document, rows)

    def parse_rows(
        self,
        document: OcrDocument,
        rows: list[tuple[str, tuple[int, ...]]],
        *,
        require_heading: bool = True,
    ) -> OcrCardDraft:
        def value[T](
            data: T | None, words: tuple[int, ...] = (), issues: tuple[OcrIssue, ...] = ()
        ) -> OcrField[T]:
            if words and min(document.words[i].confidence for i in words) < 80:
                issues = (*issues, OcrIssue.LOW_CONFIDENCE)
            return OcrField(data, words, issues)

        headings = [
            text
            for text, _ in rows
            if text.casefold() in ("покупка", "пополнение", "перевод", "операция выполнена")
            or text.casefold().startswith(("перевод ", "покупка ", "пополнение "))
        ]
        blocked = ""
        if require_heading and (
            not headings or any("история операций" in text.casefold() for text, _ in rows)
        ):
            blocked = "Макет не поддержан: выберите карточку одной операции Т-Банка."
        # Статус «Операция выполнена» может соседствовать с единственным заголовком.
        types = [h for h in headings if h.casefold() != "операция выполнена"]
        if require_heading and len(types) > 1:
            blocked = "На изображении несколько операций. Тип B ещё не поддержан."
        if any(
            re.search(
                r"^(?:(?:операция|перевод|плат[её]ж)\s+)?"
                r"(?:отклон[её]н[ао]?|отмен[её]н[ао]?|не выполнен[ао]?|"
                r"в обработке|обрабатывается)(?:\s+банком)?[.!]?$",
                text,
                re.I,
            )
            for text, _ in rows
        ):
            blocked = "Непроведённая или отменённая операция не принимается."
        money = []
        dates = []
        descriptions = []
        for text, words in rows:
            match = _MONEY.fullmatch(text)
            if match and (
                match["currency"] or match["sign"] or text.casefold().startswith("сумма:")
            ):
                money.append((match, words))
            match = _DATE.fullmatch(text)
            if match:
                dates.append((match, words))
            if text.casefold().startswith(("описание:", "получатель:", "отправитель:")):
                descriptions.append((text.split(":", 1)[1].strip(), words))
            if "комисси" in text.casefold() and re.search(r"[0-9]", text):
                blocked = "Карточка с отдельной комиссией пока не поддержана."
        if len(money) > 1:
            blocked = "Найдено несколько сумм. Такая карточка пока не поддержана."
        missing = (OcrIssue.MISSING,)
        amount: OcrField[int] = value(None, issues=missing)
        currency: OcrField[str] = value(None, issues=missing)
        direction: OcrField[CashDirection] = value(None, issues=missing)
        if len(money) == 1:
            match, words = money[0]
            try:
                minor = Money.parse(
                    match["amount"].replace("\u00a0", "").replace("\u202f", "")
                ).minor
                amount = value(
                    minor if minor > 0 else None, words, () if minor > 0 else (OcrIssue.INVALID,)
                )
            except ValidationError:
                amount = value(None, words, (OcrIssue.INVALID,))
            code = match["currency"]
            currency = value(
                "RUB" if code and code.casefold() in ("rub", "₽", "руб", "руб.") else None,
                words,
                ()
                if code and code.casefold() in ("rub", "₽", "руб", "руб.")
                else (OcrIssue.UNRECOGNIZED,),
            )
            sign = match["sign"]
            flow = (
                CashDirection.IN
                if sign == "+"
                else CashDirection.OUT
                if sign in ("-", "−")
                else None
            )
            direction = value(flow, words, () if flow else (OcrIssue.UNRECOGNIZED,))
        elif money:
            evidence = tuple(i for _, words in money for i in words)
            amount = value(None, evidence, (OcrIssue.AMBIGUOUS,))
            currency = value(None, evidence, (OcrIssue.AMBIGUOUS,))
            direction = value(None, evidence, (OcrIssue.AMBIGUOUS,))
        occurred_on: OcrField[date] = value(None, issues=missing)
        local_time: OcrField[time] = value(None, issues=missing)
        if len(dates) == 1:
            match, words = dates[0]
            year = match["year"] or match["text_year"]
            try:
                month = (
                    int(match["month"])
                    if match["month"]
                    else _MONTHS[match["month_name"].casefold()]
                )
                parsed_date = date(int(year), month, int(match["day"])) if year else None
                occurred_on = value(parsed_date, words, () if parsed_date else missing)
            except ValueError:
                occurred_on = value(None, words, (OcrIssue.INVALID,))
            if match["hour"]:
                try:
                    local_time = value(time(int(match["hour"]), int(match["minute"])), words)
                except ValueError:
                    local_time = value(None, words, (OcrIssue.INVALID,))
        elif dates:
            evidence = tuple(i for _, words in dates for i in words)
            occurred_on = value(None, evidence, (OcrIssue.AMBIGUOUS,))
            local_time = value(None, evidence, (OcrIssue.AMBIGUOUS,))
        description: OcrField[str] = value(None, issues=missing)
        if len(descriptions) == 1:
            text, words = descriptions[0]
            description = value(
                text if len(text) <= 500 else None,
                words,
                () if text and len(text) <= 500 else (OcrIssue.INVALID,),
            )
        elif descriptions:
            description = value(
                None, tuple(i for _, ws in descriptions for i in ws), (OcrIssue.AMBIGUOUS,)
            )
        return OcrCardDraft(
            document,
            "t-bank-card-v1",
            amount,
            currency,
            direction,
            occurred_on,
            local_time,
            description,
            blocked,
        )
