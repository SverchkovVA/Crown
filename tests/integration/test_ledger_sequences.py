"""Смешанная последовательность команд против независимого расчёта эффектов."""

from datetime import date, timedelta
from random import Random

from personal_finance.domain.ledger import OperationDraft, OperationKind, OperationPart
from personal_finance.domain.models import AccountKind, CategoryKind, Money


def test_mixed_commands_match_reference_balances_on_every_date(session):
    rng = Random(20261027)
    day = date(2026, 10, 27)
    accounts = [
        session.catalog.add_account("Первый", AccountKind.CASH).id,
        session.catalog.add_account("Второй", AccountKind.DEBIT).id,
    ]
    income = [c.id for c in session.catalog.categories() if c.kind == CategoryKind.INCOME]
    expense = [c.id for c in session.catalog.categories() if c.kind == CategoryKind.EXPENSE]
    ledger = session.ledger
    records = {}
    versions = {}

    def make_draft():
        kind = rng.choice([OperationKind.INCOME, OperationKind.EXPENSE, OperationKind.TRANSFER])
        when = day + timedelta(days=rng.randrange(4))
        source = rng.choice(accounts)
        amount = rng.randrange(2, 10001)
        if kind == OperationKind.TRANSFER:
            fee = rng.randrange(4)
            return OperationDraft(
                kind,
                when,
                source,
                Money(amount),
                destination_account_id=accounts[1 - accounts.index(source)],
                fee=Money(fee),
                fee_category_id=expense[0] if fee else None,
            )
        categories = income if kind == OperationKind.INCOME else expense
        if rng.choice([True, False]):
            left = rng.randrange(1, amount)
            return OperationDraft(
                kind,
                when,
                source,
                Money(amount),
                parts=(
                    OperationPart(categories[0], Money(left)),
                    OperationPart(categories[1], Money(amount - left)),
                ),
            )
        return OperationDraft(kind, when, source, Money(amount), category_id=categories[0])

    for step in range(48):
        active = [op for op in records.values() if not op.is_deleted]
        if step % 9 == 7 and active:
            previous = rng.choice(active)
            operation = ledger.delete(previous.id, previous.revision_id)
        elif step % 6 == 3 and active:
            previous = rng.choice(active)
            operation = ledger.replace(previous.id, previous.revision_id, make_draft())
        else:
            operation = ledger.add(make_draft())
        records[operation.id] = operation
        versions.setdefault(operation.id, []).append(operation)
        for offset in range(-1, 5):
            when = day + timedelta(days=offset)
            expected = dict.fromkeys(accounts, 0)
            for record in records.values():
                value = record.draft
                if record.is_deleted or value.occurred_on > when:
                    continue
                if value.kind == OperationKind.INCOME:
                    expected[value.account_id] += value.amount.minor
                elif value.kind == OperationKind.EXPENSE:
                    expected[value.account_id] -= value.amount.minor
                else:
                    expected[value.account_id] -= value.amount.minor + value.fee.minor
                    expected[value.destination_account_id] += value.amount.minor
            actual = {b.account_id: b.amount.minor for b in ledger.balances(when)}
            assert actual == expected, (step, when)
        assert ledger.history(operation.id) == versions[operation.id]
        if step % 8 == 7:
            session.check_integrity()
    assert len(ledger.operations(include_deleted=True)) == len(records)
    session.check_integrity()
