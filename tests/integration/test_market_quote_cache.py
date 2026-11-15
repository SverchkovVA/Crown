from dataclasses import replace
from datetime import timedelta
from decimal import Decimal

import pytest
from alembic import command, op

from personal_finance.application.market_price_refresh import (
    QuoteBinding,
    QuoteRefreshRow,
    QuoteStatus,
)
from personal_finance.application.vault import open_vault
from personal_finance.domain.errors import UnsupportedFormat, ValidationError, VaultError
from personal_finance.domain.ledger import OperationDraft, OperationKind
from personal_finance.domain.models import AccountKind, Money
from personal_finance.infrastructure.persistence import migration_runner
from personal_finance.infrastructure.persistence.market_quote_cache import SqlMarketQuoteCache
from tests.conftest import PASSWORD
from tests.integration.test_planning_scenario_upgrade import hashes
from tests.unit.test_market_price_refresh import DEBT, NOW, STOCK, TODAY, bond, refresh, stock


def upgrade(engine):
    config = migration_runner._configuration()
    with engine.begin() as connection:
        config.attributes["connection"] = connection
        command.upgrade(config, "0019")


def get_snapshot(session):
    return session.ledger.operations(), session.ledger.balances()


def test_prepared_migration_is_explicit_and_book_cannot_be_opened_by_0018(session):
    with session.engine.connect() as conn:
        assert (
            conn.exec_driver_sql(
                "SELECT count(*) FROM sqlite_master WHERE name='market_quote_cache'"
            ).scalar_one()
            == 0
        )
        assert (
            conn.exec_driver_sql("SELECT version_num FROM alembic_version").scalar_one() == "0018"
        )
    upgrade(session.engine)
    with session.engine.connect() as conn:
        assert (
            conn.exec_driver_sql("SELECT version_num FROM alembic_version").scalar_one() == "0019"
        )
        assert conn.exec_driver_sql("PRAGMA foreign_key_check").fetchall() == []
    root = session.root
    session.close()
    before = hashes(root)
    with pytest.raises(UnsupportedFormat):
        open_vault(root, PASSWORD)
    assert hashes(root) == before


def test_cache_preserves_dated_observations_and_ledger(session):
    real_account = session.catalog.add_account("Вымышленный счёт", AccountKind.INVESTMENT)
    session.ledger.add(OperationDraft(OperationKind.OPENING, TODAY, real_account.id, Money(10000)))
    ledger_before = get_snapshot(session)
    upgrade(session.engine)
    cache = SqlMarketQuoteCache(session.engine, session.book_id, session.mutex)
    day_before = TODAY - timedelta(days=1)
    past = stock(quoted_on=day_before, quoted_at=None)
    early = refresh(lambda _: (past,), (STOCK,))
    assert cache.save_updates(early) == early
    later = refresh(lambda _: (stock(), bond()), (STOCK, DEBT), {STOCK.security.id: past})
    assert cache.save_updates(later) == later
    assert cache.latest(STOCK) == stock()
    assert cache.latest(STOCK, through=day_before) == past
    assert cache.latest(DEBT, through=day_before) is None
    assert cache.latest(DEBT) == bond()
    assert cache.latest(DEBT).accrued_per_security == Money(0)
    other_board = QuoteBinding(STOCK.security, replace(STOCK.instrument, board="OTHER"))
    assert cache.latest(other_board) is None
    assert get_snapshot(session) == ledger_before


def test_late_writer_rechecks_freshness_inside_transaction_and_retains_other_prices(session):
    upgrade(session.engine)
    cache = SqlMarketQuoteCache(session.engine, session.book_id, session.mutex)
    first = stock(quoted_at=NOW - timedelta(minutes=2))
    newer = stock(quoted_at=NOW, value=Decimal("12.99"))
    first_party = refresh(lambda _: (first, bond()), (STOCK, DEBT))
    second_party = refresh(lambda _: (newer,), (STOCK,))
    cache.save_updates(second_party)
    results = cache.save_updates(first_party)
    assert (results[0].status, results[0].current) == (QuoteStatus.OLDER, newer)
    assert (results[1].status, results[1].current) == (QuoteStatus.UPDATED, bond())
    (replayed,) = cache.save_updates(second_party)
    assert (replayed.status, replayed.current) == (QuoteStatus.CURRENT, newer)
    disputed = refresh(lambda _: (stock(value=Decimal("33.00")),), (STOCK,))
    (rejected,) = cache.save_updates(disputed)
    assert (rejected.status, rejected.current) == (QuoteStatus.AMBIGUOUS, newer)
    (missing,) = cache.save_updates((QuoteRefreshRow(STOCK, QuoteStatus.MISSING, first),))
    assert (missing.status, missing.current) == (QuoteStatus.MISSING, newer)


def test_unbound_row_rolls_back_entire_batch(session):
    upgrade(session.engine)
    cache = SqlMarketQuoteCache(session.engine, session.book_id, session.mutex)
    valid = QuoteRefreshRow(STOCK, QuoteStatus.UPDATED, stock())
    wrong = QuoteRefreshRow(DEBT, QuoteStatus.UPDATED, stock())
    with pytest.raises(ValidationError, match="другому инструменту"):
        cache.save_updates((valid, wrong))
    assert cache.latest(STOCK) is None and cache.latest(DEBT) is None


def test_corrupted_price_is_rejected_when_reading_without_network(session):
    upgrade(session.engine)
    cache = SqlMarketQuoteCache(session.engine, session.book_id, session.mutex)
    cache.save_updates(refresh(lambda _: (stock(),), (STOCK,)))
    with session.engine.begin() as conn:
        conn.exec_driver_sql("UPDATE market_quote_cache SET value='12x'")
    with pytest.raises(VaultError, match="котировка повреждена"):
        cache.latest(STOCK)


def test_ddl_failure_rolls_back_revision_and_created_table(session, monkeypatch):
    execute = op.execute

    def fail(ddl, *args, **kwargs):
        result = execute(ddl, *args, **kwargs)
        if isinstance(ddl, str) and "CREATE TABLE market_quote_cache" in ddl:
            raise RuntimeError("Проверочное отключение")
        return result

    monkeypatch.setattr(op, "execute", fail)
    with pytest.raises(RuntimeError, match="Проверочное отключение"):
        upgrade(session.engine)
    with session.engine.connect() as conn:
        assert (
            conn.exec_driver_sql("SELECT version_num FROM alembic_version").scalar_one() == "0018"
        )
        assert (
            conn.exec_driver_sql(
                "SELECT count(*) FROM sqlite_master WHERE name='market_quote_cache'"
            ).scalar_one()
            == 0
        )
