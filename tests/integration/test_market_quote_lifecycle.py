"""Сквозные проверки будущей схемы котировок на временных зашифрованных книгах."""

import hashlib
from contextlib import closing
from datetime import timedelta

import pytest

from personal_finance.application import vault
from personal_finance.application.market_price_refresh import QuoteStatus, update_market_quote_cache
from personal_finance.domain.errors import UnsupportedFormat, VaultError
from personal_finance.domain.models import AccountKind
from personal_finance.infrastructure.backup import container
from personal_finance.infrastructure.persistence import migration_runner as migrations
from tests.conftest import BACKUP_PASSWORD, PASSWORD
from tests.integration.test_bond_amortization_storage import bond as bond_inputs
from tests.integration.test_bond_amortization_storage import with_payment
from tests.integration.test_deposit_scenario_upgrade import create_at
from tests.integration.test_planning_scenario_upgrade import hashes, state
from tests.unit.test_market_price_refresh import DEBT, NOW, STOCK, TODAY, bond, stock

SCHEMAS_0018 = migrations.SUPPORTED_SCHEMAS
RESTORED_PASSWORD = "Синтетическая фраза восстановленного кэша"


@pytest.fixture
def quote_schema(monkeypatch):
    for module in (migrations, container):
        monkeypatch.setattr(module, "SUPPORTED_SCHEMAS", (*SCHEMAS_0018, "0019"))
        monkeypatch.setattr(module, "SCHEMA_HEAD", "0019")
    monkeypatch.setattr(
        vault,
        "initialize_schema",
        lambda engine: migrations.initialize_schema(engine, target="0019"),
    )


def fingerprint(book, archive):
    return hashes(book), hashlib.sha256(archive.read_bytes()).hexdigest()


def test_explicit_update_and_two_new_folder_restores_keep_quotes_and_finances(
    tmp_path, monkeypatch, quote_schema
):
    path = tmp_path / "legacy.pfvault"
    source, _ = create_at(monkeypatch, path, "0018")
    with closing(source):
        assert source.market_quote_cache is None
        account = source.catalog.add_account("Учебный счёт", AccountKind.INVESTMENT)
        holding = source.investment_positions.save(with_payment(bond_inputs(account)))
        before = state(source)
        archive = container.create_backup(source, tmp_path / "legacy.pfbackup", BACKUP_PASSWORD)
    baseline = fingerprint(path, archive)

    target = tmp_path / "with-prices.pfvault"
    restored, recovery = container.restore_backup(
        archive, target, BACKUP_PASSWORD, RESTORED_PASSWORD
    )
    calls = []
    quoted_after_click = NOW + timedelta(seconds=1)

    def provider(codes):
        calls.append(codes)
        return stock(quoted_at=quoted_after_click), bond()

    def clock():
        assert calls == [(STOCK.instrument, DEBT.instrument)]
        return quoted_after_click + timedelta(seconds=1)

    with closing(restored):
        assert restored.schema_revision == "0019"
        assert restored.supports_corporate_events
        cache = restored.market_quote_cache
        assert cache is not None and cache.snapshot((STOCK, DEBT)) == {} and calls == []
        results = update_market_quote_cache(
            provider, cache, (STOCK, DEBT), as_of=TODAY, clock=clock
        )
        assert [row.status for row in results] == [QuoteStatus.UPDATED, QuoteStatus.UPDATED]
        assert (
            restored.investment_positions.get(holding.id) == holding and state(restored) == before
        )
        encrypted = container.create_backup(restored, tmp_path / "market.pfbackup", BACKUP_PASSWORD)
    assert fingerprint(path, archive) == baseline
    cached_baseline = fingerprint(target, encrypted)
    with closing(vault.open_vault(target, recovery, recovery=True)) as reopened:
        assert reopened.market_quote_cache.latest(STOCK).quoted_at == quoted_after_click
        assert reopened.market_quote_cache.latest(DEBT) == bond()
        assert len(calls) == 1

    with monkeypatch.context() as old:
        old.setattr(migrations, "SUPPORTED_SCHEMAS", SCHEMAS_0018)
        old.setattr(container, "SUPPORTED_SCHEMAS", SCHEMAS_0018)
        with pytest.raises(UnsupportedFormat):
            vault.open_vault(target, recovery, recovery=True)
        with pytest.raises(UnsupportedFormat):
            container.verify_backup(encrypted, BACKUP_PASSWORD)

    copy, _ = container.restore_backup(
        encrypted, tmp_path / "restored-again.pfvault", BACKUP_PASSWORD, RESTORED_PASSWORD
    )
    with closing(copy):
        assert copy.market_quote_cache.latest(STOCK).quoted_at == quoted_after_click
        assert copy.market_quote_cache.latest(DEBT) == bond()
        assert copy.investment_positions.get(holding.id) == holding
        assert state(copy) == before and len(calls) == 1
        copy.check_integrity()
    assert fingerprint(target, encrypted) == cached_baseline


def test_closed_book_cannot_receive_delayed_update(tmp_path, quote_schema):
    path = tmp_path / "closed.pfvault"
    session, _ = vault.create_vault(path, "Вымышленная книга", PASSWORD)
    before = state(session)

    def close_while_fetching(_):
        session.close()
        return (stock(),)

    with closing(session), pytest.raises(VaultError, match="Книга закрыта"):
        update_market_quote_cache(
            close_while_fetching,
            session.market_quote_cache,
            (STOCK,),
            as_of=TODAY,
            clock=lambda: NOW,
        )
    with closing(vault.open_vault(path, PASSWORD)) as reopened:
        assert reopened.market_quote_cache.latest(STOCK) is None and state(reopened) == before


def test_invalid_market_contents_stop_copy_before_publication(tmp_path, quote_schema):
    path = tmp_path / "bad-cache.pfvault"
    session, _ = vault.create_vault(path, "Вымышленная повреждённая цена", PASSWORD)
    with closing(session):
        update_market_quote_cache(
            lambda _: (stock(),),
            session.market_quote_cache,
            (STOCK,),
            as_of=TODAY,
            clock=lambda: NOW,
        )
        with session.engine.begin() as connection:
            connection.exec_driver_sql("UPDATE market_quote_cache SET value='broken'")
        output = tmp_path / "should-not-exist.pfbackup"
        with pytest.raises(VaultError, match="котировка повреждена"):
            container.create_backup(session, output, BACKUP_PASSWORD)
        assert not output.exists()
        assert not list(tmp_path.glob(".pf-backup-*"))
