"""Проверка пробного формата 3 и совместимости форматов 1 и 2."""

import hashlib
import json
from contextlib import closing
from dataclasses import replace
from uuid import uuid4

import pytest
from sqlalchemy.exc import IntegrityError

from personal_finance.application.vault import open_vault
from personal_finance.domain.errors import UnsupportedFormat, ValidationError
from personal_finance.domain.investment_portfolio import report_saved_portfolio
from personal_finance.domain.investment_positions import (
    InvestmentPositionInputs,
    investment_position_from_json,
    investment_position_json,
)
from personal_finance.domain.investments import StockSplit, TradeKind
from personal_finance.domain.models import AccountKind, Currency, Money
from personal_finance.infrastructure.backup import container
from personal_finance.infrastructure.persistence import migration_runner
from personal_finance.infrastructure.persistence.investment_position_repository import (
    SqlInvestmentPositions,
)
from tests.conftest import BACKUP_PASSWORD
from tests.integration.test_deposit_scenario_upgrade import create_at
from tests.integration.test_planning_scenario_upgrade import hashes, state
from tests.integration.test_stock_split_lifecycle import BASE_SCHEMAS, restore
from tests.integration.test_stock_split_lifecycle import split_schema as split_schema
from tests.unit.test_bond_amortization import NEXT, principal
from tests.unit.test_investment_positions import inputs
from tests.unit.test_investments import ACCOUNT, BOND, TODAY, identifier, trade


def bond(account=ACCOUNT):
    bought = trade(1, TradeKind.BUY, 3, 270000, 9, account=account, security=BOND, accrued=9000)
    return InvestmentPositionInputs(account.id, BOND, (bought,))


def with_payment(data):
    return replace(data, amortizations=(replace(principal(), account_id=data.account_id),))


def test_canonical_formats_1_and_2_keep_preparation_bytes():
    prior = inputs()
    split = StockSplit(identifier(259), prior.account_id, prior.security.id, TODAY, 3, 2)
    cases = (
        (prior, "e85653df69aeddb5e251bda0919885dcf61d7679dc206433f190e9ff0ca824af"),
        (
            replace(prior, splits=(split,)),
            "33f147d6be28a303b324897e96cface49a488f91b286235cc0ef084e37e89cd3",
        ),
    )
    for position, expected in cases:
        raw = investment_position_json(position, allow_corporate_events=True)
        assert hashlib.sha256(raw.encode()).hexdigest() == expected
        assert investment_position_from_json(raw, allow_corporate_events=True) == position


def test_canonical_format_3_requires_scope_and_exact_integer_minor_units():
    updated = with_payment(bond())
    with pytest.raises(ValidationError, match="совместимая"):
        investment_position_json(updated)
    raw = investment_position_json(updated, allow_corporate_events=True)
    data = json.loads(raw)
    assert data["format"] == 3 and data["amortizations"][0]["face_after_minor"] == 75000
    assert "splits" not in data
    assert investment_position_from_json(raw, allow_corporate_events=True) == updated
    with pytest.raises(ValidationError, match="формат"):
        investment_position_from_json(raw)
    for changed in (
        raw.replace('"quantity":3', '"quantity":3.0', 1),
        raw.replace('"face_after_minor":75000', '"face_after_minor":true'),
        raw.replace('"face_before_minor":100000', '"face_before_minor":100000.0'),
        raw.replace('"format":3', '"format":2'),
        raw.replace('"amortizations":', '"splits":[],"amortizations":'),
        raw + "\n",
    ):
        with pytest.raises(ValidationError, match="формат"):
            investment_position_from_json(changed, allow_corporate_events=True)
    dollars = Currency("USD", 2)
    payment = replace(
        updated.amortizations[0],
        face_before=Money(100000, dollars),
        face_after=Money(75000, dollars),
    )
    with pytest.raises(ValidationError, match="валюту"):
        replace(updated, amortizations=(payment,))


def test_format_3_cannot_be_added_to_0017_and_survives_a_second_encrypted_copy(
    tmp_path, monkeypatch, split_schema
):
    source = tmp_path / "source.pfvault"
    old, _ = create_at(monkeypatch, source, "0017")
    original_command = str(uuid4())
    with closing(old):
        account = old.catalog.add_account("Учебный облигационный счёт", AccountKind.INVESTMENT)
        original = old.investment_positions.save(bond(account), command_id=original_command)
        updated = with_payment(original.inputs)
        command = str(uuid4())
        parameters = dict(
            identifier=original.id,
            expected_revision_id=original.revision_id,
            command_id=command,
        )
        with pytest.raises(ValidationError, match="совместимая"):
            old.investment_positions.save(updated, **parameters)
        forced = SqlInvestmentPositions(
            old.engine, old.book_id, old.replica_id, old.mutex, allow_corporate_events=True
        )
        with pytest.raises(IntegrityError, match="CHECK constraint"):
            forced.save(updated, **parameters)
        assert old.investment_positions.history(original.id) == [original]
        before = state(old)
        archive = container.create_backup(old, tmp_path / "original.pfbackup", BACKUP_PASSWORD)
    original_hash = hashes(source), hashlib.sha256(archive.read_bytes()).hexdigest()

    staged, _ = restore(archive, tmp_path / "staged.pfvault")
    with closing(staged):
        assert staged.schema_revision == "0018"
        service = staged.investment_positions
        assert service.save(original.inputs, command_id=original_command) == original
        saved = service.save(updated, **parameters)
        assert service.history(saved.id) == [original, saved]
        assert state(staged) == before
        staged.check_integrity()
        staged_archive = container.create_backup(
            staged, tmp_path / "staged.pfbackup", BACKUP_PASSWORD
        )
    contents = container.inspect_backup(staged_archive, BACKUP_PASSWORD)
    assert contents.source_schema == "0018" and contents.investment_positions == 1
    assert original_hash == (hashes(source), hashlib.sha256(archive.read_bytes()).hexdigest())

    with monkeypatch.context() as old_reader:
        old_reader.setattr(migration_runner, "SUPPORTED_SCHEMAS", BASE_SCHEMAS)
        old_reader.setattr(container, "SUPPORTED_SCHEMAS", BASE_SCHEMAS)
        with pytest.raises(UnsupportedFormat):
            container.verify_backup(staged_archive, BACKUP_PASSWORD)
    destination = tmp_path / "restored.pfvault"
    final, pf1 = restore(staged_archive, destination)
    with closing(final):
        assert final.investment_positions.save(updated, **parameters) == saved
        assert state(final) == before
        final.check_integrity()
    with closing(open_vault(destination, pf1, recovery=True)) as opened:
        assert opened.investment_positions.history(saved.id) == [original, saved]
        (row,) = report_saved_portfolio((account,), (saved,), NEXT).rows
        assert row.position.cost_minor == 211507
        (receipt,) = row.valuation.portfolio.amortizations
        assert receipt.event.principal_minor == 75000 and receipt.realized_minor == 7498
