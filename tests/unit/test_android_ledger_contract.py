"""Общий учебный журнал совпадает с проводками настольного Crown."""

import json
from datetime import date
from pathlib import Path
from typing import Any

from personal_finance.domain.ledger import LedgerRole, OperationDraft, OperationKind
from personal_finance.domain.models import Money

_CATEGORY = "00000000-0000-4000-8000-000000000701"
_FEE_CATEGORY = "00000000-0000-4000-8000-000000000702"


def test_android_ledger_sample_matches_desktop_postings() -> None:
    project = Path(__file__).resolve().parents[2]
    fixture = project / "shared/android-fixtures/ledger-v1-sample.json"
    document: dict[str, Any] = json.loads(fixture.read_bytes())
    assert document["format"] == 1
    assert document["currency"] == "RUB"
    month = document["month"]
    balances = {account["id"]: 0 for account in document["accounts"]}
    income = 0
    expense = 0
    for row in document["operations"]:
        kind = OperationKind(row["kind"])
        minor = row["amount_minor"]
        fee = row.get("fee_minor", "0")
        assert isinstance(minor, str) and isinstance(fee, str)
        draft = OperationDraft(
            kind=kind,
            occurred_on=date.fromisoformat(row["date"]),
            account_id=row["account_id"],
            amount=Money(int(minor)),
            category_id=_CATEGORY
            if kind in (OperationKind.INCOME, OperationKind.EXPENSE)
            else None,
            destination_account_id=row.get("destination_id"),
            fee=Money(int(fee)),
            fee_category_id=_FEE_CATEGORY if int(fee) else None,
        )
        for posting in draft.postings():
            if draft.occurred_on.isoformat()[:7] > month:
                continue
            if posting.role == LedgerRole.ASSET:
                assert posting.reference_id is not None
                balances[posting.reference_id] += posting.amount.minor
            elif draft.occurred_on.isoformat()[:7] == month:
                if posting.role == LedgerRole.INCOME:
                    income -= posting.amount.minor
                elif posting.role == LedgerRole.EXPENSE:
                    expense += posting.amount.minor

    expected = document["expected"]
    assert {account: str(minor) for account, minor in balances.items()} == expected["balances"]
    assert str(income) == expected["income_minor"]
    assert str(expense) == expected["expense_minor"]
