"""Подготовка версионного хранения календарей платежей."""

import re

from alembic import op

revision = "0016"
down_revision = "0015"
branch_labels = None
depends_on = None

_TABLE = "planning_scenario_revisions"
_STAGING = "planning_scenario_revisions_0016"
_KIND_CHECK = "scenario_kind IN ('budget','savings_goal')"
_CURRENCY_CHECK = "AND json_type(scenario_json,'$.currency')='object'"
_COALESCE_END = ",0) ELSE 0 END)"
_COLUMNS = "revision_id,scenario_kind,name,name_key,scenario_json"


def _quoted(name: str) -> str:
    return '"' + name.replace('"', '""') + '"'


def _expanded_ddl(ddl: str) -> str:
    if any(ddl.count(part) != 1 for part in (_KIND_CHECK, _CURRENCY_CHECK, _COALESCE_END)):
        raise RuntimeError("Проверки условий планирования отличаются от схемы 0015.")
    ddl = ddl.replace(_KIND_CHECK, "scenario_kind IN ('budget','savings_goal','payment_calendar')")
    ddl = ddl.replace(
        _CURRENCY_CHECK,
        """AND ((scenario_kind='payment_calendar'
            AND json_extract(scenario_json,'$.algorithm')='calendar-anchors-v1'
            AND json_type(scenario_json,'$.schedules')='array'
            AND json_array_length(scenario_json,'$.schedules') BETWEEN 1 AND 1000)
            OR (json_type(scenario_json,'$.currency')='object'""",
    )
    ddl = ddl.replace(_COALESCE_END, "))" + _COALESCE_END)
    ddl, count = re.subn(
        r'\ACREATE TABLE (?:"planning_scenario_revisions"|planning_scenario_revisions)(?=\s*\()',
        f'CREATE TABLE "{_STAGING}"',
        ddl,
        count=1,
        flags=re.I,
    )
    if count != 1:
        raise RuntimeError("Не найдено ожидаемое определение условий планирования.")
    return ddl


def upgrade() -> None:
    connection = op.get_bind()
    count: int = connection.exec_driver_sql(f"SELECT count(*) FROM {_TABLE}").scalar_one()
    if count and connection.exec_driver_sql("PRAGMA foreign_keys").scalar_one():
        raise RuntimeError("Заполненная книга обновляется в промежуточной копии восстановления.")
    ddl: str = connection.exec_driver_sql(
        "SELECT sql FROM sqlite_master WHERE type='table' AND name=?", (_TABLE,)
    ).scalar_one()
    staged = _expanded_ddl(ddl)
    raw_objects = connection.exec_driver_sql(
        "SELECT type,name,tbl_name,sql FROM sqlite_master "
        "WHERE type IN ('trigger','index','view') AND sql IS NOT NULL ORDER BY rowid"
    ).fetchall()
    objects = [
        (str(kind), str(name), str(table), str(sql)) for kind, name, table, sql in raw_objects
    ]
    references = re.compile(r"\bplanning_scenario_revisions\b", re.I)
    if any(kind == "view" and references.search(sql) for kind, _, _, sql in objects):
        raise RuntimeError("Представление зависит от условий планирования; переход остановлен.")
    triggers = [
        (name, sql)
        for kind, name, table, sql in objects
        if kind == "trigger" and (table == _TABLE or references.search(sql))
    ]
    indexes = [sql for kind, _, table, sql in objects if kind == "index" and table == _TABLE]
    if not any(name == "revision_payload_valid" for name, _ in triggers):
        raise RuntimeError("Не найдена защита запечатанной версии условий планирования.")
    op.execute(staged)
    op.execute(f"INSERT INTO {_STAGING} ({_COLUMNS}) SELECT {_COLUMNS} FROM {_TABLE}")
    difference: int = connection.exec_driver_sql(
        f"SELECT EXISTS(SELECT * FROM {_TABLE} EXCEPT SELECT * FROM {_STAGING})"
        f"+ EXISTS(SELECT * FROM {_STAGING} EXCEPT SELECT * FROM {_TABLE})"
    ).scalar_one()
    if (
        difference
        or connection.exec_driver_sql(f"SELECT count(*) FROM {_STAGING}").scalar_one() != count
    ):
        raise RuntimeError("Перенос условий планирования не подтверждён.")
    for name, _ in triggers:
        op.execute("DROP TRIGGER " + _quoted(name))
    op.execute(f"DROP TABLE {_TABLE}")
    op.execute(f"ALTER TABLE {_STAGING} RENAME TO {_TABLE}")
    for sql in indexes:
        op.execute(sql)
    for _, sql in triggers:
        op.execute(sql)

    op.execute("""CREATE TRIGGER planning_calendar_schedule_valid
        BEFORE UPDATE OF sealed ON entity_revisions
        WHEN NEW.sealed=1 AND OLD.sealed=0 AND EXISTS (
            SELECT 1 FROM planning_scenario_revisions p, json_each(p.scenario_json,'$.schedules') s
            WHERE p.revision_id=NEW.id AND p.scenario_kind='payment_calendar'
            AND CASE WHEN s.type='object' THEN COALESCE(
                json_type(s.value,'$.name')='text'
                AND length(trim(json_extract(s.value,'$.name'))) BETWEEN 1 AND 120
                AND json_type(s.value,'$.amount_minor')='integer'
                AND typeof(json_extract(s.value,'$.amount_minor'))='integer'
                AND json_extract(s.value,'$.amount_minor')>0
                AND json_type(s.value,'$.currency')='object'
                AND json_type(s.value,'$.currency.code')='text'
                AND length(json_extract(s.value,'$.currency.code'))=3
                AND json_extract(s.value,'$.currency.code') NOT GLOB '*[^A-Z]*'
                AND json_type(s.value,'$.currency.exponent')='integer'
                AND json_extract(s.value,'$.currency.exponent') BETWEEN 0 AND 8
                AND json_extract(s.value,'$.direction') IN ('income','expense')
                AND json_type(s.value,'$.first_on')='text'
                AND json_extract(s.value,'$.first_on') BETWEEN '0001-01-01' AND '9999-12-31'
                AND date(json_extract(s.value,'$.first_on'))=json_extract(s.value,'$.first_on')
                AND json_extract(s.value,'$.unit') IN ('once','days','weeks','months')
                AND json_type(s.value,'$.interval')='integer'
                AND json_extract(s.value,'$.interval') BETWEEN 1 AND 1200
                AND (json_type(s.value,'$.last_on')='null'
                    OR (json_type(s.value,'$.last_on')='text'
                        AND date(json_extract(s.value,'$.last_on'))
                            =json_extract(s.value,'$.last_on')
                        AND json_extract(s.value,'$.last_on')>=json_extract(s.value,'$.first_on')))
                AND (json_extract(s.value,'$.unit')<>'once'
                    OR (json_extract(s.value,'$.interval')=1
                        AND json_type(s.value,'$.last_on')='null')), 0)
                ELSE 0 END=0
        )
        BEGIN SELECT RAISE(ABORT, 'invalid payment schedule'); END""")


def downgrade() -> None:
    raise RuntimeError("Понижение схемы запрещено; используйте исходную книгу.")
