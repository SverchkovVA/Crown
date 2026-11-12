"""Подготовка схемы 0017 для инвестиционных позиций; в рабочую цепочку не включена."""

import re

from alembic import op

revision = "0017"
down_revision = "0016"
branch_labels = None
depends_on = None

_KIND = "investment_position"
_TABLE = "investment_position_revisions"
_STAGING = "entities_0017"
_OLD_KINDS = (
    "institution",
    "account",
    "category",
    "operation",
    "period",
    "tag",
    "saved_filter",
    "category_rule",
    "deposit_scenario",
    "bonus_program",
    "bonus_entry",
    "planning_scenario",
)


def _quoted(name: str) -> str:
    return '"' + name.replace('"', '""') + '"'


def _expanded_entities(ddl: str) -> str:
    previous = r"\s*,\s*".join(re.escape(f"'{kind}'") for kind in _OLD_KINDS)
    pattern = r"(?i:kind\s+IN)\s*\(\s*" + previous + r"\s*\)"
    expanded = "kind IN (" + ",".join(f"'{kind}'" for kind in (*_OLD_KINDS, _KIND)) + ")"
    ddl, changes = re.subn(pattern, expanded, ddl)
    if changes != 1:
        raise RuntimeError("Проверка типов сущностей отличается от схемы 0016.")
    ddl, changes = re.subn(
        r'\ACREATE TABLE (?:"entities"|entities)(?=\s*\()',
        f'CREATE TABLE "{_STAGING}"',
        ddl,
        count=1,
        flags=re.I,
    )
    if changes != 1:
        raise RuntimeError("Не найдено ожидаемое определение сущностей.")
    return ddl


def _expanded_payload_guard(sql: str) -> str:
    suffix = ") BEGIN SELECT RAISE(ABORT, 'missing typed payload'); END"
    if sql.count(suffix) != 1 or "planning_scenario_revisions" not in sql:
        raise RuntimeError("Проверка состава версии отличается от схемы 0016.")
    return sql.replace(
        suffix,
        f""" OR EXISTS (
            SELECT 1 FROM entities e JOIN {_TABLE} p ON p.revision_id=NEW.id
            WHERE e.id=NEW.entity_id AND e.kind='{_KIND}'
        )"""
        + suffix,
    )


def _create_position_table() -> None:
    op.execute("""CREATE TABLE investment_position_revisions (
        revision_id TEXT PRIMARY KEY REFERENCES entity_revisions(id),
        account_id TEXT NOT NULL REFERENCES entities(id),
        security_id TEXT NOT NULL CHECK (length(security_id)=36),
        security_code TEXT NOT NULL CHECK (length(security_code) BETWEEN 1 AND 32),
        position_json TEXT NOT NULL,
        CHECK (length(CAST(position_json AS BLOB)) BETWEEN 2 AND 4194304),
        CHECK (CASE WHEN json_valid(position_json) THEN
            json_type(position_json,'$.format') IS 'integer'
            AND json_extract(position_json,'$.format')=1
            AND json_extract(position_json,'$.account_id') IS account_id
            AND json_type(position_json,'$.security') IS 'object'
            AND json_extract(position_json,'$.security.id') IS security_id
            AND json_extract(position_json,'$.security.code') IS security_code
            AND COALESCE(json_extract(position_json,'$.security.kind')
                IN ('stock','bond','fund'),0)
            AND json_type(position_json,'$.security.currency.code') IS 'text'
            AND json_type(position_json,'$.security.currency.exponent') IS 'integer'
            AND json_extract(position_json,'$.security.currency.exponent') BETWEEN 0 AND 8
            AND json_type(position_json,'$.events') IS 'array'
            AND json_array_length(position_json,'$.events') BETWEEN 1 AND 10000
            AND json_type(position_json,'$.prices') IS 'array'
            AND json_array_length(position_json,'$.prices') BETWEEN 0 AND 10000
        ELSE 0 END)
    ) STRICT""")
    op.execute(
        "CREATE INDEX ix_investment_position_scope "
        "ON investment_position_revisions(account_id,security_code)"
    )


def _create_position_guards() -> None:
    op.execute("""CREATE TRIGGER investment_position_payload_scope
        BEFORE INSERT ON investment_position_revisions
        WHEN NOT EXISTS (
            SELECT 1 FROM entity_revisions r JOIN entities position ON position.id=r.entity_id
            JOIN entities a ON a.id=NEW.account_id AND a.book_id=position.book_id
            JOIN account_revisions value ON value.revision_id=a.accepted_revision_id
            JOIN entity_revisions ar ON ar.id=value.revision_id
            WHERE r.id=NEW.revision_id AND r.sealed=0 AND position.kind='investment_position'
            AND a.kind='account' AND value.kind='investment' AND ar.sealed=1
            AND (r.is_deleted=1 OR ar.is_deleted=0)
            AND value.currency=json_extract(NEW.position_json,'$.security.currency.code')
        )
        BEGIN SELECT RAISE(ABORT, 'invalid investment position scope'); END""")
    for action in ("UPDATE", "DELETE"):
        op.execute(
            f"""CREATE TRIGGER investment_position_payload_{action.lower()}
            BEFORE {action} ON investment_position_revisions
            BEGIN SELECT RAISE(ABORT, 'immutable investment position payload'); END"""
        )
    op.execute("""CREATE TRIGGER investment_position_keep_identity
        BEFORE UPDATE OF accepted_revision_id ON entities
        WHEN NEW.kind='investment_position' AND EXISTS (
            SELECT 1 FROM investment_position_revisions before_data
            JOIN investment_position_revisions after_data
                ON after_data.revision_id=NEW.accepted_revision_id
            WHERE before_data.revision_id=OLD.accepted_revision_id AND (
                before_data.account_id<>after_data.account_id
                OR before_data.security_id<>after_data.security_id
                OR json_extract(before_data.position_json,'$.security.kind')
                    <>json_extract(after_data.position_json,'$.security.kind')
                OR json_extract(before_data.position_json,'$.security.currency')
                    <>json_extract(after_data.position_json,'$.security.currency')
            )
        )
        BEGIN SELECT RAISE(ABORT, 'investment position identity changed'); END""")
    op.execute("""CREATE TRIGGER investment_position_unique_active
        BEFORE UPDATE OF accepted_revision_id ON entities
        WHEN NEW.kind='investment_position' AND EXISTS (
            SELECT 1 FROM investment_position_revisions incoming
            JOIN entity_revisions new_revision ON new_revision.id=incoming.revision_id
            JOIN investment_position_revisions existing ON existing.account_id=incoming.account_id
                AND existing.security_code=incoming.security_code
            JOIN entity_revisions old_revision ON old_revision.id=existing.revision_id
            JOIN entities other ON other.accepted_revision_id=existing.revision_id
            WHERE incoming.revision_id=NEW.accepted_revision_id
                AND new_revision.is_deleted=0 AND old_revision.is_deleted=0
                AND other.book_id=NEW.book_id AND other.kind='investment_position'
                AND other.id<>NEW.id
        )
        BEGIN SELECT RAISE(ABORT, 'duplicate active investment position'); END""")


def upgrade() -> None:
    connection = op.get_bind()
    count: int = connection.exec_driver_sql("SELECT count(*) FROM entities").scalar_one()
    if count and connection.exec_driver_sql("PRAGMA foreign_keys").scalar_one():
        raise RuntimeError("Заполненная книга обновляется в промежуточной копии восстановления.")
    ddl: str = connection.exec_driver_sql(
        "SELECT sql FROM sqlite_master WHERE type='table' AND name='entities'"
    ).scalar_one()
    expanded = _expanded_entities(ddl)
    raw_rows = connection.exec_driver_sql(
        "SELECT type,name,tbl_name,sql FROM sqlite_master "
        "WHERE type IN ('trigger','index','view') AND sql IS NOT NULL ORDER BY rowid"
    ).fetchall()
    rows = [(str(kind), str(name), str(table), str(sql)) for kind, name, table, sql in raw_rows]
    reference = re.compile(r"\bentities\b", re.I)
    if any(kind == "view" and reference.search(sql) for kind, _, _, sql in rows):
        raise RuntimeError("Неизвестное представление ссылается на сущности.")
    triggers = [
        (name, sql) for kind, name, _, sql in rows if kind == "trigger" and reference.search(sql)
    ]
    indexes = [sql for kind, _, table, sql in rows if kind == "index" and table == "entities"]
    payload = next((sql for name, sql in triggers if name == "revision_payload_valid"), None)
    if payload is None:
        raise RuntimeError("Не найдена проверка состава версии.")
    payload = _expanded_payload_guard(payload)
    op.execute(expanded)
    op.execute(f"INSERT INTO {_STAGING} SELECT * FROM entities")
    difference: int = connection.exec_driver_sql(
        f"SELECT EXISTS(SELECT * FROM entities EXCEPT SELECT * FROM {_STAGING}) "
        f"+ EXISTS(SELECT * FROM {_STAGING} EXCEPT SELECT * FROM entities)"
    ).scalar_one()
    if (
        difference
        or connection.exec_driver_sql(f"SELECT count(*) FROM {_STAGING}").scalar_one() != count
    ):
        raise RuntimeError("Перенос исходных сущностей не подтверждён.")
    for name, _ in triggers:
        op.execute("DROP TRIGGER " + _quoted(name))
    op.execute("DROP TABLE entities")
    op.execute(f"ALTER TABLE {_STAGING} RENAME TO entities")
    for index in indexes:
        op.execute(index)
    _create_position_table()
    for name, sql in triggers:
        op.execute(payload if name == "revision_payload_valid" else sql)
    _create_position_guards()


def downgrade() -> None:
    raise RuntimeError("Понижение схемы инвестиционных позиций не поддерживается.")
