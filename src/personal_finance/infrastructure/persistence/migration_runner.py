"""Установка новой схемы и обновление только промежуточной восстановленной базы."""

from pathlib import Path

from alembic import command
from alembic.config import Config
from alembic.runtime.migration import MigrationContext
from sqlalchemy import Engine

from personal_finance.domain.errors import UnsupportedFormat, VaultError

SCHEMA_HEAD = "0011"
SUPPORTED_SCHEMAS = (
    "0002",
    "0003",
    "0004",
    "0005",
    "0006",
    "0007",
    "0008",
    "0009",
    "0010",
    SCHEMA_HEAD,
)


def _configuration() -> Config:
    config = Config()
    config.set_main_option("script_location", str(Path(__file__).with_name("migrations")))
    return config


def initialize_schema(engine: Engine, *, target: str = SCHEMA_HEAD) -> None:
    if target not in SUPPORTED_SCHEMAS:
        raise UnsupportedFormat("Указана неподдерживаемая схема новой базы.")
    config = _configuration()
    with engine.begin() as connection:
        existing: int = connection.exec_driver_sql(
            "SELECT count(*) FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
        ).scalar_one()
        if existing:
            raise UnsupportedFormat(
                "Установка начальной схемы разрешена только для новой пустой базы."
            )
        config.attributes["connection"] = connection
        command.upgrade(config, target)


def supported_schema(engine: Engine) -> str:
    with engine.connect() as connection:
        heads = MigrationContext.configure(connection).get_current_heads()
    if len(heads) != 1 or heads[0] not in SUPPORTED_SCHEMAS:
        raise UnsupportedFormat(
            "Версия базы отличается от поддерживаемой. "
            "Изменения не выполнены; используйте совместимую версию приложения."
        )
    return heads[0]


def require_current_schema(engine: Engine) -> None:
    if supported_schema(engine) != SCHEMA_HEAD:
        raise UnsupportedFormat("Для новых функций восстановите копию в новую папку.")


def upgrade_restored_schema(engine: Engine, expected: str) -> None:
    """Вызывается только до публикации новой папки в restore_backup, не при открытии."""
    current = supported_schema(engine)
    if current != expected:
        raise UnsupportedFormat("Схема базы не соответствует описанию резервной копии.")
    if current == SCHEMA_HEAD:
        return
    config = _configuration()
    with engine.connect() as connection:
        driver = connection.connection.driver_connection
        if driver is None:
            raise VaultError("Соединение промежуточной базы уже закрыто.")
        # SQLite требует отключить FK до транзакционного пересоздания entities.
        # Соединение принадлежит только промежуточной копии; исходная книга не открывается.
        driver.execute("PRAGMA foreign_keys=OFF")
        try:
            with connection.begin():
                config.attributes["connection"] = connection
                command.upgrade(config, SCHEMA_HEAD)
                if connection.exec_driver_sql("PRAGMA foreign_key_check").fetchall():
                    raise VaultError("Проверка связей после обновления копии не пройдена.")
        finally:
            driver.execute("PRAGMA foreign_keys=ON")
