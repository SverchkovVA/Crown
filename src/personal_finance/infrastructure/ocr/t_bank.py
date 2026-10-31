"""Консервативный прототип карточки Т-Банка; банк выбирается явно."""

from dataclasses import replace

from personal_finance.domain.ocr import OcrDocument
from personal_finance.domain.ocr_cards import OcrCardDraft
from personal_finance.infrastructure.ocr.rows import document_rows
from personal_finance.infrastructure.ocr.rub_fields import parse_rub_fields


class TBankCardAdapter:
    def parse(self, document: OcrDocument) -> OcrCardDraft:
        return self.parse_rows(document, document_rows(document))

    def parse_rows(
        self,
        document: OcrDocument,
        rows: list[tuple[str, tuple[int, ...]]],
        *,
        require_heading: bool = True,
    ) -> OcrCardDraft:
        result = parse_rub_fields(document, rows, adapter_id="t-bank-card-v1")
        if not require_heading:
            return result
        headings = [
            text
            for text, _ in rows
            if text.casefold() in ("покупка", "пополнение", "перевод", "операция выполнена")
            or text.casefold().startswith(("перевод ", "покупка ", "пополнение "))
        ]
        blocked = ""
        if not headings or any("история операций" in text.casefold() for text, _ in rows):
            blocked = "Макет не поддержан: выберите карточку одной операции Т-Банка."
        types = [h for h in headings if h.casefold() != "операция выполнена"]
        if len(types) > 1:
            blocked = "На изображении несколько операций. Выберите просмотр истории."
        return replace(result, blocked_reason=result.blocked_reason or blocked)
