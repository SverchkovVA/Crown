import hashlib
from datetime import date

import pytest

from personal_finance.application.analytics import Analytics
from personal_finance.domain.analytics import ReportPeriod, ReportScope, report_sources
from personal_finance.domain.errors import VaultError
from personal_finance.domain.ledger import OperationDraft, OperationKind
from personal_finance.domain.models import RUB, AccountKind, CategoryKind, Money


def test_comparison_is_readonly_and_keeps_versions_after_edit_and_close(session):
    account = session.catalog.add_account("Вымышленный сравнительный счёт", AccountKind.CASH)
    categories = {
        kind: next(c.id for c in session.catalog.categories() if c.kind == kind)
        for kind in CategoryKind
    }
    for kind, day, minor in [
        (OperationKind.INCOME, date(2026, 9, 15), 10000),
        (OperationKind.EXPENSE, date(2026, 10, 15), 3000),
    ]:
        session.ledger.add(
            OperationDraft(
                kind,
                day,
                account.id,
                Money(minor),
                categories[
                    CategoryKind.INCOME if kind == OperationKind.INCOME else CategoryKind.EXPENSE
                ],
            )
        )

    def fingerprints():
        return tuple(
            hashlib.sha256((session.root / "data" / name).read_bytes()).hexdigest()
            for name in ["ledger.sqlite3", "key.json"]
        )

    before = fingerprints()
    service = Analytics(session.ledger, session.mutex, session.ensure_open)
    first, second = (
        ReportPeriod(date(2026, 9, 1), date(2026, 9, 30)),
        ReportPeriod(date(2026, 10, 1), date(2026, 10, 31)),
    )
    result = service.compare(first, second, ReportScope(account_ids=frozenset({account.id})))
    assert fingerprints() == before
    assert result.totals[0].difference_delta == -13000
    expense = result.after.sources[0]
    session.ledger.delete(expense.id, expense.revision_id)
    assert service.compare(first, second).after.sources == ()
    session.close()
    assert report_sources(result.after, RUB)[0].operation.revision_id == expense.revision_id
    assert result.after.totals[0].expense_minor == 3000
    with pytest.raises(VaultError):
        service.compare(first, second)
