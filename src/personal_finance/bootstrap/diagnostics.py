"""Диагностика проверочной сборки без имён пользователя, путей и секретов."""

import platform
import struct
import sys

import alembic
import nacl
import sqlalchemy
from PySide6.QtCore import qVersion
from sqlcipher3 import dbapi2 as sqlcipher

from personal_finance import __version__


def runtime_info() -> dict[str, object]:
    connection = sqlcipher.connect(":memory:")
    try:
        cipher_version = connection.execute("PRAGMA cipher_version").fetchone()[0]
    finally:
        connection.close()
    return {
        "application": __version__,
        "python": platform.python_version(),
        "platform": sys.platform,
        "architecture": platform.machine(),
        "pointer_bits": struct.calcsize("P") * 8,
        "frozen": bool(getattr(sys, "frozen", False)),
        "qt": qVersion(),
        "sqlcipher": cipher_version,
        "sqlite": sqlcipher.sqlite_version,
        "sqlalchemy": sqlalchemy.__version__,
        "alembic": alembic.__version__,
        "pynacl": nacl.__version__,
    }
