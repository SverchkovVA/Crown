"""Соединения SQLCipher. Перехода к незашифрованному драйверу нет."""

import os
from collections.abc import Callable
from contextlib import closing
from pathlib import Path
from typing import Any

from sqlalchemy import Engine, create_engine, event
from sqlalchemy.pool import NullPool
from sqlcipher3 import dbapi2 as sqlcipher

from personal_finance.domain.errors import VaultError


def connect_database(path: Path, key: bytes, *, create: bool = False) -> Any:
    if len(key) != 32:
        raise VaultError("Недопустимый ключ хранилища.")
    if not create and (not path.is_file() or path.is_symlink()):
        raise VaultError("Файл базы отсутствует или является ссылкой.")
    if create:
        descriptor = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        os.close(descriptor)
        connection = sqlcipher.connect(
            path.resolve().as_uri() + "?mode=rw", uri=True, isolation_level=None
        )
    else:
        connection = sqlcipher.connect(
            path.resolve().as_uri() + "?mode=rw", uri=True, isolation_level=None
        )
    try:
        # В SQL попадает только шестнадцатеричный случайный ключ; пользовательский текст исключён.
        connection.execute(f'''PRAGMA key = "x'{key.hex()}'"''')
        if not connection.execute("PRAGMA cipher_version").fetchone():
            raise VaultError("В установленной библиотеке отсутствует SQLCipher.")
        connection.execute("SELECT count(*) FROM sqlite_master").fetchone()
        connection.execute("PRAGMA foreign_keys=ON")
        connection.execute("PRAGMA busy_timeout=5000")
        connection.execute("PRAGMA temp_store=MEMORY")
        connection.execute("PRAGMA cipher_memory_security=ON")
        connection.execute("PRAGMA trusted_schema=OFF")
        connection.execute("PRAGMA synchronous=FULL")
        if connection.execute("PRAGMA foreign_keys").fetchone()[0] != 1:
            raise VaultError("Не удалось включить проверку внешних ключей.")
        return connection
    except Exception:
        connection.close()
        raise


def make_engine(path: Path, key: bytes | Callable[[], bytes]) -> Engine:
    engine = create_engine(
        "sqlite+pysqlite://",
        module=sqlcipher,
        creator=lambda: connect_database(path, key() if callable(key) else key),
        poolclass=NullPool,
        echo=False,
        hide_parameters=True,
    )

    @event.listens_for(engine, "begin")
    def explicit_begin(connection: Any) -> None:
        # sqlite3 legacy mode не должен оставлять DDL вне настоящей транзакции.
        connection.exec_driver_sql("BEGIN")

    return engine


def check_database(path: Path, key: bytes) -> None:
    with closing(connect_database(path, key)) as connection:
        if connection.execute("PRAGMA integrity_check").fetchall() != [("ok",)]:
            raise VaultError("Проверка целостности базы не пройдена.")
        if connection.execute("PRAGMA cipher_integrity_check").fetchall():
            raise VaultError("Проверка шифрованных страниц не пройдена.")
        if connection.execute("PRAGMA foreign_key_check").fetchall():
            raise VaultError("Нарушены связи данных в базе.")


def encrypted_snapshot(source: Path, target: Path, key: bytes) -> None:
    if target.exists():
        raise VaultError("Файл снимка уже существует.")
    with (
        closing(connect_database(source, key)) as source_connection,
        closing(connect_database(target, key, create=True)) as target_connection,
    ):
        source_connection.backup(target_connection)
        target_connection.execute("PRAGMA journal_mode=DELETE")
    check_database(target, key)
