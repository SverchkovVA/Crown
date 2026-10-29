"""Явная валюта и неизменяемый аудит, только вымышленные книги."""

import json
from dataclasses import replace
from datetime import date

import pytest
from sqlalchemy import text

from personal_finance.domain.errors import ValidationError
from personal_finance.domain.ledger import OperationDraft, OperationKind
from personal_finance.domain.models import AccountKind, CategoryKind, Money
from personal_finance.domain.ocr_cards import CardCorrections, CashDirection, review_card
from personal_finance.domain.ocr_imports import (
    OcrEvidence,
    OcrImportStatus,
    OcrSelection,
    canonical,
    make_ocr_evidence,
)
from personal_finance.domain.ocr_profiles import UNIVERSAL_PROFILE
from personal_finance.infrastructure.ocr.generic import GenericCardAdapter
from tests.ocr_fixtures import document


def _selection(session, currency="рублей"):
    account = (
        session.catalog.accounts()[0]
        if session.catalog.accounts()
        else session.catalog.add_account("Вымышленный счёт", AccountKind.CASH)
    )
    category = next(c.id for c in session.catalog.categories() if c.kind == CategoryKind.EXPENSE)
    raw = GenericCardAdapter().parse(
        document(
            ("Детали операции", "Списано: 473,15 " + currency, "22.10.2026 09:18"),
            image_sha256="c" * 64,
        )
    )
    assert not raw.blocked_reason and raw.currency.value == "RUB" and raw.amount.value == 47315
    reviewed = review_card(
        raw,
        CardCorrections("473,15", "RUB", CashDirection.OUT, "22.10.2026", "09:18"),
        confirmed_fields=frozenset({"amount", "currency", "direction", "occurred_on"}),
        single_completed_operation=True,
    )
    return OcrSelection(
        make_ocr_evidence("d" * 64, UNIVERSAL_PROFILE, reviewed),
        account.id,
        OperationDraft(
            OperationKind.EXPENSE, date(2026, 10, 22), account.id, Money(47315), category
        ),
    )


def test_literal_currency_is_audited_and_repeat_never_adds_movement(session):
    value = _selection(session)
    data = json.loads(value.evidence.payload)
    assert data["adapter"] == "generic-card-v2" and data["fields"]["currency"]["value"] == "RUB"
    assert any("рублей" in w["text"] for w in data["words"])
    assert OcrEvidence(value.evidence.payload) == value.evidence
    session.ocr_importing.confirm(value)
    assert (
        len(session.ledger.operations()) == 1
        and session.ledger.operations()[0].draft.amount.minor == 47315
    )
    assert session.ocr_importing.preview(value).status == OcrImportStatus.DUPLICATE
    with pytest.raises(ValidationError):
        session.ocr_importing.confirm(value)
    assert len(session.ledger.operations()) == 1
    session.check_integrity()


def test_old_adapter_audit_is_immutable_and_new_context_conflicts(session):
    current = _selection(session, "руб.")
    data = json.loads(current.evidence.payload)
    data["adapter"] = "generic-card-v1"
    old = replace(current, evidence=OcrEvidence(canonical(data)))
    session.ocr_importing.confirm(old)
    with session.engine.connect() as connection:
        before = tuple(connection.execute(text("SELECT * FROM ocr_records")).fetchall())
    assert session.ocr_importing.preview(current).status == OcrImportStatus.CONFLICT
    with pytest.raises(ValidationError):
        session.ocr_importing.confirm(current)
    with session.engine.connect() as connection:
        after = tuple(connection.execute(text("SELECT * FROM ocr_records")).fetchall())
    assert before == after and len(session.ledger.operations()) == 1
    assert session.ocr_importing.preview(old).status == OcrImportStatus.DUPLICATE
    session.check_integrity()
