"""Установка новой базы; существующая неизвестная схема никогда не обновляется молча."""

from pathlib import Path

from alembic import command
from alembic.config import Config
from alembic.runtime.migration import MigrationContext
from sqlalchemy import Engine

from personal_finance.domain.errors import UnsupportedFormat

SCHEMA_HEAD = "0002"


def initialize_schema(engine: Engine) -> None:
    config = Config()
    config.set_main_option("script_location", str(Path(__file__).with_name("migrations")))
    with engine.begin() as connection:
        existing: int = connection.exec_driver_sql(
            "SELECT count(*) FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
        ).scalar_one()
        if existing:
            raise UnsupportedFormat(
                "Установка начальной схемы разрешена только для новой пустой базы."
            )
        config.attributes["connection"] = connection
        command.upgrade(config, SCHEMA_HEAD)


def require_current_schema(engine: Engine) -> None:
    with engine.connect() as connection:
        heads = MigrationContext.configure(connection).get_current_heads()
    if heads != (SCHEMA_HEAD,):
        raise UnsupportedFormat(
            "Версия базы отличается от поддерживаемой. "
            "Изменения не выполнены; используйте совместимую версию приложения."
        )
