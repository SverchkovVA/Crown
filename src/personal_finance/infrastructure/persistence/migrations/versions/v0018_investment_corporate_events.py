"""Подготовленная схема позиций с дроблением акций."""

import re

from alembic import op

revision = "0018"
down_revision = "0017"
branch_labels = None
depends_on = None

_TABLE = "investment_position_revisions"
_STAGING = "investment_position_revisions_0018"
_FORMAT_CHECK = "AND json_extract(position_json,'$.format')=1"
_NEW_CHECK = """AND (
        (json_extract(position_json,'$.format')=1 AND json_type(position_json,'$.splits') IS NULL)
        OR (
            json_extract(position_json,'$.format')=2
            AND json_extract(position_json,'$.security.kind') IS 'stock'
            AND json_type(position_json,'$.splits') IS 'array'
            AND json_array_length(position_json,'$.splits') BETWEEN 1 AND 9999
            AND json_array_length(position_json,'$.events')
                + json_array_length(position_json,'$.splits') <= 10000
        )
    )"""


def upgrade() -> None:
    connection = op.get_bind()
    ddl: str = connection.exec_driver_sql(
        "SELECT sql FROM sqlite_master WHERE type='table' AND name=?", (_TABLE,)
    ).scalar_one()
    staged, changes = re.subn(
        rf'\ACREATE TABLE (?:"{_TABLE}"|{_TABLE})(?=\s*\()',
        f'CREATE TABLE "{_STAGING}"',
        ddl,
        count=1,
        flags=re.I,
    )
    if changes != 1 or staged.count(_FORMAT_CHECK) != 1:
        raise RuntimeError("Определение инвестиционных позиций отличается от схемы 0017.")
    rows = connection.exec_driver_sql(
        "SELECT type,name,tbl_name,sql FROM sqlite_master "
        "WHERE type IN ('trigger','index','view') AND sql IS NOT NULL ORDER BY rowid"
    )
    reference = re.compile(r"\binvestment_position_revisions\b", re.I)
    triggers: dict[str, str] = {}
    indexes: list[str] = []
    for kind_value, name_value, table_value, sql_value in rows:
        kind, name, table, sql = str(kind_value), str(name_value), str(table_value), str(sql_value)
        if kind == "view" and reference.search(sql):
            raise RuntimeError("Представление ссылается на инвестиционные позиции.")
        if kind == "trigger" and reference.search(sql):
            triggers[name] = sql
        if kind == "index" and table == _TABLE:
            indexes.append(sql)

    op.execute(staged.replace(_FORMAT_CHECK, _NEW_CHECK))
    op.execute(f"INSERT INTO {_STAGING} SELECT * FROM {_TABLE}")
    difference: int = connection.exec_driver_sql(
        f"SELECT EXISTS(SELECT * FROM {_TABLE} EXCEPT SELECT * FROM {_STAGING}) "
        f"+ EXISTS(SELECT * FROM {_STAGING} EXCEPT SELECT * FROM {_TABLE})"
    ).scalar_one()
    if difference:
        raise RuntimeError("Перенос исходных версий инвестиционных позиций не подтверждён.")
    for name in triggers:
        quoted = '"' + name.replace('"', '""') + '"'
        op.execute(f"DROP TRIGGER {quoted}")
    op.execute(f"DROP TABLE {_TABLE}")
    op.execute(f"ALTER TABLE {_STAGING} RENAME TO {_TABLE}")
    for sql in indexes:
        op.execute(sql)
    for sql in triggers.values():
        op.execute(sql)
    if connection.exec_driver_sql("PRAGMA foreign_key_check").fetchone() is not None:
        raise RuntimeError("Нарушены связи инвестиционных версий после миграции.")


def downgrade() -> None:
    raise RuntimeError("Понижение схемы инвестиционных позиций не поддерживается.")
