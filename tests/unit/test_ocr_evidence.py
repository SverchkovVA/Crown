"""Независимые вымышленные проверки финансового происхождения OCR."""

import json
from dataclasses import replace
from datetime import date
from uuid import uuid4

import pytest

from personal_finance.application.ocr_cards import OcrCardPreview
from personal_finance.application.ocr_importing import OcrImporting
from personal_finance.domain.errors import ValidationError
from personal_finance.domain.ledger import OperationDraft, OperationKind
from personal_finance.domain.models import Currency, Money
from personal_finance.domain.ocr import LoadedOcrImage
from personal_finance.domain.ocr_cards import CardCorrections, CashDirection, OcrField, review_card
from personal_finance.domain.ocr_imports import (
    MAX_OCR_EVIDENCE_BYTES,
    OcrEvidence,
    OcrSelection,
    canonical,
    make_ocr_evidence,
)
from tests.ocr_fixtures import card, image

DAY = date(2026, 10, 22)
MINOR = 473015
CONFIRMED = frozenset({"amount", "currency", "direction", "occurred_on"})
LINES = (
    "Т-Банк",
    "Перевод",
    "-4 730,15 RUB",
    "22 октября 2026 в 09:18",
    "Описание: Синтетическое назначение",
    "Вне выбранных полей: исключённый текст",
)


def reviewed(*, source=None, amount="4730,15", direction=CashDirection.OUT):
    return review_card(
        source or card(LINES),
        CardCorrections(
            amount, "RUB", direction, "22.10.2026", "09:18", "Синтетическое назначение"
        ),
        confirmed_fields=CONFIRMED,
        single_completed_operation=True,
    )


def evidence(**kwargs):
    return make_ocr_evidence("3" * 64, "Т-Банк", reviewed(**kwargs))


def test_evidence_roundtrip_keeps_only_selected_words_and_exact_source_metadata():
    source = reviewed()
    value = make_ocr_evidence("3" * 64, "Т-Банк", source)
    data = value.data
    refs = {i for field in source.source.fields() for i in field.word_indices}
    assert {word["index"] for word in data["words"]} == refs
    assert "исключённый текст" not in value.payload
    assert data["file_sha256"] == "3" * 64
    assert data["image_sha256"] == source.source.document.image_sha256
    assert len(data["document_sha256"]) == 64 and data["engine"]["models"]
    assert data["words"][0]["box"] == [50, 240, 900, 50]
    assert OcrEvidence(value.payload) == value
    restored = value.reviewed
    assert restored.amount == Money(MINOR) and restored.direction == CashDirection.OUT
    assert restored.occurred_on == DAY and restored.changed_fields == ()
    assert "4730" not in repr(value) and "назначение" not in repr(value)
    detached = value.data
    detached["reviewed"]["minor"] = 1
    assert value.reviewed.amount.minor == MINOR


def test_manual_changes_are_replayed_and_included_in_evidence():
    value = evidence(amount="4730,16")
    assert value.reviewed.amount == Money(MINOR + 1)
    assert value.data["changed_fields"] == ["amount"]
    assert value.data["fields"]["amount"]["value"] == MINOR


@pytest.mark.parametrize(
    "field,value",
    [
        ("version", True),
        ("bank", "Другой профиль"),
        ("file_sha256", "invalid"),
        ("image_sha256", "A" * 64),
        ("width", True),
        ("height", 0),
        ("changed_fields", ["amount"]),
    ],
)
def test_malformed_top_level_evidence_is_rejected_without_echo(field, value):
    data = evidence().data
    data[field] = value
    with pytest.raises(ValidationError) as error:
        OcrEvidence(canonical(data))
    assert "Синтетическое назначение" not in str(error.value)


@pytest.mark.parametrize(
    "path,value",
    [
        (("reviewed", "minor"), True),
        (("reviewed", "minor"), 1.5),
        (("reviewed", "minor"), -1),
        (("reviewed", "currency"), "USD"),
        (("reviewed", "occurred_on"), "2026-02-31"),
        (("reviewed", "local_time"), "09:18:01"),
        (("reviewed", "local_time"), "09:18:00+03:00"),
        (("fields", "amount", "value"), True),
        (("fields", "amount", "words"), [9999]),
        (("fields", "direction", "value"), "sideways"),
    ],
)
def test_malformed_or_forged_financial_values_are_rejected(path, value):
    data = evidence().data
    node = data
    for part in path[:-1]:
        node = node[part]
    node[path[-1]] = value
    with pytest.raises(ValidationError):
        OcrEvidence(canonical(data))


def test_changed_fields_cannot_hide_manual_amount_correction():
    data = evidence(amount="4730,16").data
    data["changed_fields"] = []
    with pytest.raises(ValidationError):
        OcrEvidence(canonical(data))


@pytest.mark.parametrize(
    "change", ["duplicate_index", "outside_box", "nan_confidence", "model_hash"]
)
def test_malformed_words_or_models_cannot_enter_the_audit(change):
    data = evidence().data
    if change == "duplicate_index":
        data["words"][1]["index"] = data["words"][0]["index"]
    elif change == "outside_box":
        data["words"][0]["box"] = [2000, 20, 100, 20]
    elif change == "nan_confidence":
        data["words"][0]["confidence"] = "NaN"
    else:
        data["engine"]["models"][0][1] = "wrong"
    with pytest.raises(ValidationError):
        OcrEvidence(canonical(data))


def test_noncanonical_unknown_key_and_excessive_evidence_are_rejected():
    value = evidence()
    with pytest.raises(ValidationError):
        OcrEvidence(json.dumps(value.data, ensure_ascii=False))
    data = value.data
    data["unknown"] = "Не сохранять"
    with pytest.raises(ValidationError):
        OcrEvidence(canonical(data))
    with pytest.raises(ValidationError):
        OcrEvidence("x" * (MAX_OCR_EVIDENCE_BYTES + 1))


def test_metadata_only_file_change_keeps_image_row_identity_but_not_payload():
    first = evidence()
    data = first.data
    data["file_sha256"] = "4" * 64
    second = OcrEvidence(canonical(data))
    assert first.payload != second.payload
    assert first.source_key == second.source_key and first.content_hash == second.content_hash
    altered = evidence(amount="4730,16")
    assert altered.source_key == first.source_key and altered.content_hash != first.content_hash


def test_blocked_card_or_missing_money_region_cannot_become_financial_evidence():
    value = reviewed()
    with pytest.raises(ValidationError):
        make_ocr_evidence(
            "3" * 64,
            "Т-Банк",
            replace(value, source=replace(value.source, blocked_reason="Неполный макет")),
        )
    without = replace(value.source, amount=OcrField(MINOR))
    with pytest.raises(ValidationError):
        make_ocr_evidence("3" * 64, "Т-Банк", reviewed(source=without))


def test_confusable_currency_remains_an_explicit_manual_change():
    source = card((*LINES[:2], "-4 730,15 Р", *LINES[3:]))
    value = evidence(source=source)
    assert value.data["fields"]["currency"]["value"] is None
    assert value.reviewed.amount == Money(MINOR)
    assert "currency" in value.reviewed.changed_fields


def test_selection_exactly_matches_movement_and_protects_repr():
    account, category = str(uuid4()), str(uuid4())
    draft = OperationDraft(OperationKind.EXPENSE, DAY, account, Money(MINOR), category)
    value = OcrSelection(evidence(), account, draft)
    assert "4730" not in repr(value) and "evidence" not in repr(value)
    for other in (
        replace(draft, amount=Money(MINOR + 1)),
        replace(draft, occurred_on=date(2026, 10, 23)),
    ):
        with pytest.raises(ValidationError):
            OcrSelection(evidence(), account, other)
    with pytest.raises(ValidationError):
        OcrSelection(evidence(), str(uuid4()), draft)
    with pytest.raises(ValidationError):
        OcrSelection(
            evidence(),
            account,
            replace(
                draft, amount=Money(MINOR, Currency("USD", 2)), fee=Money(0, Currency("USD", 2))
            ),
        )
    with pytest.raises(ValidationError):
        OcrSelection(
            evidence(), account, OperationDraft(OperationKind.OPENING, DAY, account, Money(-MINOR))
        )


def test_transfer_uses_observed_account_side_and_exact_total_including_fee():
    source, destination, fee_category = str(uuid4()), str(uuid4()), str(uuid4())
    transfer = OperationDraft(
        OperationKind.TRANSFER, DAY, source, Money(MINOR), destination_account_id=destination
    )
    assert OcrSelection(evidence(), source, transfer).draft == transfer
    incoming = evidence(direction=CashDirection.IN)
    assert OcrSelection(incoming, destination, transfer).draft == transfer
    with pytest.raises(ValidationError):
        OcrSelection(incoming, source, transfer)
    with pytest.raises(ValidationError):
        OcrSelection(
            evidence(), source, replace(transfer, fee=Money(15), fee_category_id=fee_category)
        )
    gross = replace(transfer, amount=Money(MINOR - 15), fee=Money(15), fee_category_id=fee_category)
    assert OcrSelection(evidence(), source, gross).draft == gross


def test_link_requires_both_identifiers_and_application_rejects_foreign_review_source():
    account, category = str(uuid4()), str(uuid4())
    draft = OperationDraft(OperationKind.EXPENSE, DAY, account, Money(MINOR), category)
    with pytest.raises(ValidationError):
        OcrSelection(evidence(), account, draft, existing_operation_id=str(uuid4()))
    original = reviewed()
    loaded = LoadedOcrImage("3" * 64, image())
    preview = OcrCardPreview(loaded, original.source)
    value = OcrImporting.prepare(preview, original, account, draft)
    assert value.draft == draft
    foreign = reviewed(source=card(LINES, image_sha256="f" * 64))
    with pytest.raises(ValidationError):
        OcrImporting.prepare(preview, foreign, account, draft)


def test_evidence_rejects_unreferenced_words_instead_of_persisting_unselected_text():
    data = evidence().data
    extra = dict(data["words"][0])
    extra.update(index=999, text="Невыбранный фон изображения", line=[1, 1, 999], number=1)
    data["words"].append(extra)
    with pytest.raises(ValidationError):
        OcrEvidence(canonical(data))


def test_deep_malformed_json_has_neutral_validation_error():
    with pytest.raises(ValidationError):
        OcrEvidence("[" * 10000 + "0" + "]" * 10000)


@pytest.mark.parametrize("value", [95, 95.5, True, None, [], {}, "Infinity", "-1", "101"])
def test_confidence_never_coerces_external_numbers_or_invalid_decimal_values(value):
    data = evidence().data
    data["words"][0]["confidence"] = value
    with pytest.raises(ValidationError) as error:
        OcrEvidence(canonical(data))
    assert "Синтетическое назначение" not in str(error.value)


@pytest.mark.parametrize(
    "path,value",
    [
        (("fields", "amount", "issues"), ""),
        (("fields", "amount", "issues"), {"missing": True}),
        (("engine", "models"), [{"eng": "не модель", "f" * 64: "не хеш"}]),
        (("fields", "amount", "words"), {"2": True}),
        (("fields", "description", "value"), ["не строка"]),
        (("reviewed", "description"), ["не строка"]),
        (("reviewed", "local_time"), 918),
        (("words", 0, "line"), [True, 1, 1]),
        (("words", 0, "number"), True),
        (("words", 0, "box"), [50, 240, True, 50]),
    ],
)
def test_typed_json_shapes_are_rejected_instead_of_iterable_coercion(path, value):
    data = evidence().data
    node = data
    for part in path[:-1]:
        node = node[part]
    node[path[-1]] = value
    with pytest.raises(ValidationError) as error:
        OcrEvidence(canonical(data))
    assert "Синтетическое назначение" not in str(error.value)
