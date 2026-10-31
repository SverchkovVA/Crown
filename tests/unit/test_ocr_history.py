from dataclasses import replace

import pytest

from personal_finance.application.ocr import Ocr
from personal_finance.application.ocr_cards import CardRecognition
from personal_finance.domain.errors import ValidationError
from personal_finance.domain.ocr import LoadedOcrImage
from personal_finance.domain.ocr_cards import CashDirection, OcrHistoryDraft, OcrIssue
from personal_finance.infrastructure.ocr.t_bank import TBankCardAdapter
from personal_finance.infrastructure.ocr.t_bank_history import TBankHistoryAdapter
from tests.ocr_fixtures import history_document, image


def test_history_separates_rows_and_keeps_global_word_evidence():
    doc = history_document()
    result = TBankHistoryAdapter().parse(doc)
    assert len(result.cards) == 2 and not result.blocked_reason
    first, second = result.cards
    assert first.amount.value == 123456 and second.amount.value == 5000
    assert first.direction.value == CashDirection.OUT and second.direction.value == CashDirection.IN
    assert first.currency.value == "RUB" and second.currency.value is None
    assert first.document is doc and second.document is doc
    assert set(first.amount.word_indices).isdisjoint(second.amount.word_indices)
    assert first.occurred_on.value.isoformat() == "2026-10-27"
    assert first.description.value == "Магазин"


@pytest.mark.parametrize("header", ["27 октября", "Сегодня", "Вчера"])
def test_year_and_relative_headers_are_not_resolved_from_machine_clock(header):
    result = TBankHistoryAdapter().parse(history_document(header=header))
    assert len(result.cards) == 2
    assert all(card.occurred_on.value is None for card in result.cards)
    assert all(card.occurred_on.word_indices for card in result.cards)
    if header in ("Сегодня", "Вчера"):
        assert OcrIssue.UNRECOGNIZED in result.cards[0].occurred_on.issues


@pytest.mark.parametrize("value", ["-1 23,45 RUB", "-12.345,67 RUB", "-О,50 RUB"])
def test_invalid_grouping_never_becomes_a_smaller_valid_tail(value):
    result = TBankHistoryAdapter().parse(history_document(values=(value,)))
    assert result.cards == () and result.blocked_reason


def test_no_date_header_or_amount_on_left_is_not_a_known_history():
    for doc in (history_document(header="Неизвестный макет"), history_document(amount_left=100)):
        result = TBankHistoryAdapter().parse(doc)
        assert not result.cards and result.blocked_reason


def test_multiple_right_amounts_are_reported_as_unassigned_instead_of_arbitrary_choice():
    result = TBankHistoryAdapter().parse(history_document(values=("-10 RUB +20 RUB",)))
    assert not result.cards and result.unassigned_fragments == 2


def test_equal_amounts_on_two_rows_are_preserved_as_two_proposals():
    result = TBankHistoryAdapter().parse(history_document(values=("-50 RUB", "-50 RUB")))
    assert len(result.cards) == 2
    assert result.cards[0].amount.value == result.cards[1].amount.value == 5000


def test_row_at_image_edge_cannot_be_confirmed_as_complete():
    result = TBankHistoryAdapter().parse(history_document(values=("-50 RUB",), bottom=True))
    assert result.cards[0].blocked_reason


def test_history_source_and_limits_are_validated():
    doc = history_document()
    result = TBankHistoryAdapter().parse(doc)
    with pytest.raises(ValidationError):
        replace(
            result, cards=(replace(result.cards[0], document=replace(doc, image_sha256="f" * 64)),)
        )
    with pytest.raises(ValidationError):
        OcrHistoryDraft(doc, (), True)


def test_application_selects_separate_history_adapter_and_rejects_foreign_result(tmp_path):
    class Engine:
        def recognize(self, value):
            return history_document(image_sha256=value.sha256)

    loaded = LoadedOcrImage("3" * 64, image())
    service = CardRecognition(Ocr(Engine()), lambda path: loaded, TBankCardAdapter())
    assert not service.supports_history
    with pytest.raises(ValidationError):
        service.history(tmp_path / "unused")
    service = CardRecognition(
        Ocr(Engine()),
        lambda path: loaded,
        TBankCardAdapter(),
        history_adapter=TBankHistoryAdapter(),
    )
    assert service.supports_history and len(service.history(tmp_path / "unused").history.cards) == 2


def test_history_with_pending_status_or_separate_fee_stays_blocked():
    from personal_finance.domain.ocr import OcrBox, OcrWord

    doc = history_document()
    extra = OcrWord("В обработке", OcrBox(50, 500, 150, 30), doc.words[0].confidence, (1, 1, 99), 1)
    assert TBankHistoryAdapter().parse(replace(doc, words=(*doc.words, extra))).blocked_reason
    fee = replace(
        doc,
        words=tuple(replace(w, text="Комиссия") if w.text == "Магазин" else w for w in doc.words),
    )
    assert TBankHistoryAdapter().parse(fee).blocked_reason


@pytest.mark.parametrize("count", [100, 101])
def test_history_has_a_hard_limit_instead_of_silent_truncation(count):
    from decimal import Decimal

    from personal_finance.domain.ocr import OcrBox, OcrDocument, OcrWord
    from tests.ocr_fixtures import ENGINE

    words = [OcrWord("27.10.2026", OcrBox(50, 5, 100, 10), Decimal(95), (1, 1, 1), 1)]
    words.extend(
        OcrWord("-1,00 RUB", OcrBox(700, 40 + i * 12, 150, 10), Decimal(95), (1, 1, i + 2), 1)
        for i in range(count)
    )
    doc = OcrDocument(image().sha256, 1080, 1400, ENGINE, tuple(words))
    result = TBankHistoryAdapter().parse(doc)
    assert (
        (len(result.cards) == count and not result.blocked_reason)
        if count == 100
        else (not result.cards and result.blocked_reason)
    )


def test_unknown_next_date_header_does_not_reuse_old_tbank_date():
    from decimal import Decimal

    from personal_finance.domain.ocr import OcrBox, OcrWord

    doc = history_document(values=("-1 RUB",))
    extra = (
        OcrWord("28 окт. 2026", OcrBox(20, 400, 400, 20), Decimal(95), (1, 1, 90), 1),
        OcrWord("-2 RUB", OcrBox(700, 450, 300, 20), Decimal(95), (1, 1, 91), 1),
    )
    result = TBankHistoryAdapter().parse(replace(doc, words=(*doc.words, *extra)))
    assert len(result.cards) == 1 and result.unassigned_fragments == 1
