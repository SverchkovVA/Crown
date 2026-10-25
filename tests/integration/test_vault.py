import json
import sqlite3
from pathlib import Path
from uuid import uuid4

import pytest
from sqlalchemy import text

from personal_finance.application.vault import create_vault, open_vault
from personal_finance.domain.errors import FinanceError, InvalidCredentials, UnsupportedFormat
from personal_finance.domain.models import AccountKind
from tests.conftest import PASSWORD


def test_persistent_encrypted_book_cannot_be_read_by_sqlite(tmp_path: Path) -> None:
    path = tmp_path / "private.pfvault"
    session, recovery = create_vault(path, "Очень личная книга", PASSWORD)
    session.catalog.add_account("Секретное имя счёта", AccountKind.CASH)
    session.close()
    database = path / "data" / "ledger.sqlite3"
    raw = database.read_bytes()
    assert not raw.startswith(b"SQLite format 3")
    assert "Очень личная книга".encode() not in raw
    assert "Секретное имя счёта".encode() not in raw
    with sqlite3.connect(database) as connection, pytest.raises(sqlite3.DatabaseError):
        connection.execute("SELECT * FROM sqlite_master").fetchall()
    reopened = open_vault(path, recovery, recovery=True)
    try:
        assert reopened.catalog.accounts()[0].name == "Секретное имя счёта"
        reopened.check_integrity()
    finally:
        reopened.close()


def test_wrong_credentials_and_bad_kdf_do_not_modify_database(tmp_path: Path) -> None:
    path = tmp_path / "book.pfvault"
    session, _ = create_vault(path, "Книга", PASSWORD)
    session.close()
    database = path / "data" / "ledger.sqlite3"
    original = database.read_bytes()
    with pytest.raises(InvalidCredentials):
        open_vault(path, "совсем другая парольная фраза")
    manifest = path / "data" / "key.json"
    data = json.loads(manifest.read_bytes())
    data["kdf"]["memlimit"] = 2**60
    manifest.write_text(json.dumps(data), encoding="utf-8")
    with pytest.raises(UnsupportedFormat):
        open_vault(path, PASSWORD)
    assert database.read_bytes() == original


def test_exclusive_access_and_closed_session(session) -> None:
    catalog = session.catalog
    with pytest.raises(FinanceError, match="уже открыта"):
        open_vault(session.root, PASSWORD)
    session.close()
    with pytest.raises(FinanceError, match="закрыта"):
        catalog.add_account("Нельзя записать", AccountKind.CASH)


def test_existing_directory_is_never_replaced(tmp_path: Path) -> None:
    target = tmp_path / "existing"
    target.mkdir()
    marker = target / "important.txt"
    marker.write_text("сохранить", encoding="utf-8")
    with pytest.raises(FinanceError):
        create_vault(target, "Книга", PASSWORD)
    assert marker.read_text(encoding="utf-8") == "сохранить"


def test_unknown_schema_is_not_migrated(session) -> None:
    root = session.root
    with session.engine.begin() as connection:
        connection.execute(text("UPDATE alembic_version SET version_num='9999'"))
    session.close()
    with pytest.raises(UnsupportedFormat):
        open_vault(root, PASSWORD)


def test_missing_database_is_not_created(session) -> None:
    root = session.root
    session.close()
    database = root / "data" / "ledger.sqlite3"
    database.rename(database.with_suffix(".saved"))
    with pytest.raises(FinanceError):
        open_vault(root, PASSWORD)
    assert not database.exists()


def test_initial_schema_and_connection_safety(session) -> None:
    with session.engine.connect() as connection:
        assert connection.exec_driver_sql("PRAGMA foreign_keys").scalar_one() == 1
        assert connection.exec_driver_sql("PRAGMA synchronous").scalar_one() == 2
        assert connection.exec_driver_sql("PRAGMA journal_mode").scalar_one() == "wal"
        strict = connection.exec_driver_sql("PRAGMA table_list").mappings().all()
        assert next(row for row in strict if row["name"] == "books")["strict"] == 1
    with pytest.raises(RuntimeError), session.engine.begin() as connection:
        connection.exec_driver_sql("CREATE TABLE rollback_probe (id TEXT)")
        raise RuntimeError("имитация отказа")
    with session.engine.connect() as connection:
        assert (
            connection.execute(
                text("SELECT count(*) FROM sqlite_master WHERE name='rollback_probe'")
            ).scalar_one()
            == 0
        )


def test_foreign_key_rejects_invalid_book(session) -> None:
    from sqlalchemy.exc import IntegrityError

    with pytest.raises(IntegrityError), session.engine.begin() as connection:
        connection.execute(
            text("INSERT INTO replicas (id,book_id) VALUES (:id,:book)"),
            {"id": str(uuid4()), "book": str(uuid4())},
        )
