import hashlib
from dataclasses import replace
from datetime import date

import pytest
from sqlalchemy import text

from personal_finance.application import vault
from personal_finance.application.analytics import Analytics
from personal_finance.domain.analytics import ReportPeriod, ReportScope
from personal_finance.domain.analytics_cashflow import CashSourceKind, cash_flow_sources
from personal_finance.domain.errors import VaultError
from personal_finance.domain.ledger import OperationDraft, OperationKind, OperationPart
from personal_finance.domain.models import RUB, AccountKind, CategoryKind, Money
from personal_finance.infrastructure.persistence.migration_runner import initialize_schema
from tests.conftest import PASSWORD


def test_cash_flow_matches_ledger_without_changing_book_and_keeps_closed_snapshot(session):
    first = session.catalog.add_account("Вымышленный первый", AccountKind.CASH)
    second = session.catalog.add_account("Вымышленный второй", AccountKind.CASH)
    categories = session.catalog.categories()
    income = next(c.id for c in categories if c.kind == CategoryKind.INCOME)
    expenses = [c.id for c in categories if c.kind == CategoryKind.EXPENSE]
    session.ledger.add(
        OperationDraft(OperationKind.OPENING, date(2026, 9, 30), first.id, Money(100000))
    )
    session.ledger.add(
        OperationDraft(OperationKind.OPENING, date(2026, 10, 5), second.id, Money(10000))
    )
    earned = session.ledger.add(
        OperationDraft(OperationKind.INCOME, date(2026, 10, 15), first.id, Money(50000), income)
    )
    expense = session.ledger.add(
        OperationDraft(
            OperationKind.EXPENSE,
            date(2026, 10, 16),
            first.id,
            Money(20000),
            parts=(
                OperationPart(expenses[0], Money(13000)),
                OperationPart(expenses[1], Money(7000)),
            ),
        )
    )
    session.ledger.add(
        OperationDraft(
            OperationKind.TRANSFER,
            date(2026, 10, 17),
            first.id,
            Money(30000),
            destination_account_id=second.id,
            fee=Money(100),
            fee_category_id=expenses[0],
        )
    )
    session.ledger.add(
        OperationDraft(
            OperationKind.REFUND,
            date(2026, 10, 20),
            second.id,
            Money(5000),
            expenses[0],
            refund_of=expense.id,
            refund_of_revision_id=expense.revision_id,
        )
    )

    def hashes():
        return tuple(
            hashlib.sha256((session.root / "data" / name).read_bytes()).hexdigest()
            for name in ["ledger.sqlite3", "key.json"]
        )

    original = hashes()
    period = ReportPeriod(date(2026, 10, 1), date(2026, 10, 31))
    service = Analytics(session.ledger, session.mutex, session.ensure_open)
    report = service.cash_flow(period)
    by_account = {v.account_id: v.totals.closing_minor for v in report.accounts}
    balances = {v.account_id: v.amount.minor for v in session.ledger.balances(period.end)}
    assert by_account == balances == {first.id: 99900, second.id: 45000} and hashes() == original
    assert (
        sum(v.minor for v in cash_flow_sources(report, RUB, CashSourceKind.THROUGH_END)) == 144900
    )
    assert (
        service.cash_flow(period, ReportScope(account_ids=frozenset({second.id})))
        .totals[0]
        .closing_minor
        == 45000
    )
    assert hashes() == original
    session.ledger.replace(
        earned.id, earned.revision_id, replace(earned.draft, amount=Money(60000))
    )
    assert service.cash_flow(period).totals[0].closing_minor == 154900
    session.close()
    assert report.totals[0].closing_minor == 144900 and earned.revision_id in {
        v[1] for v in report.operation_references
    }
    with pytest.raises(VaultError):
        service.cash_flow(period)


def test_cash_flow_reads_legacy_schema3_without_migration(tmp_path, monkeypatch):
    with monkeypatch.context() as patch:
        patch.setattr(
            vault, "initialize_schema", lambda engine: initialize_schema(engine, target="0003")
        )
        opened, _ = vault.create_vault(
            tmp_path / "legacy.pfvault", "Вымышленная старая книга", PASSWORD
        )
    try:
        account = opened.catalog.add_account("Старый вымышленный счёт", AccountKind.CASH)
        categories = {
            kind: next(c.id for c in opened.catalog.categories() if c.kind == kind)
            for kind in CategoryKind
        }
        opened.ledger.add(
            OperationDraft(OperationKind.OPENING, date(2026, 9, 30), account.id, Money(100))
        )
        opened.ledger.add(
            OperationDraft(
                OperationKind.INCOME,
                date(2026, 10, 15),
                account.id,
                Money(50),
                categories[CategoryKind.INCOME],
            )
        )
        opened.ledger.add(
            OperationDraft(
                OperationKind.EXPENSE,
                date(2026, 10, 16),
                account.id,
                Money(20),
                categories[CategoryKind.EXPENSE],
            )
        )
        before = tuple(
            hashlib.sha256((opened.root / "data" / name).read_bytes()).hexdigest()
            for name in ["ledger.sqlite3", "key.json"]
        )
        report = Analytics(opened.ledger, opened.mutex, opened.ensure_open).cash_flow(
            ReportPeriod(date(2026, 10, 1), date(2026, 10, 31))
        )
        total = report.totals[0]
        assert (
            total.opening_minor,
            total.incoming_minor,
            total.outgoing_minor,
            total.closing_minor,
        ) == (100, 50, 20, 130)
        with opened.engine.connect() as connection:
            assert (
                connection.execute(text("SELECT version_num FROM alembic_version")).scalar_one()
                == "0003"
            )
        assert opened.schema_revision == "0003"
        assert before == tuple(
            hashlib.sha256((opened.root / "data" / name).read_bytes()).hexdigest()
            for name in ["ledger.sqlite3", "key.json"]
        )
    finally:
        opened.close()
