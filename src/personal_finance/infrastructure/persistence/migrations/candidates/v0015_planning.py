"""Кандидат 0015: условия бюджета и цели; действующая схема не меняется."""

import re

from alembic import op

revision = "0015"
down_revision = "0014"
branch_labels = None
depends_on = None


def _quoted(name: str) -> str:
    return '"' + name.replace('"', '""') + '"'


def upgrade() -> None:
    connection = op.get_bind()
    count: int = connection.exec_driver_sql("SELECT count(*) FROM entities").scalar_one()
    if count and connection.exec_driver_sql("PRAGMA foreign_keys").scalar_one():
        raise RuntimeError(
            "Заполненная книга обновляется только в промежуточной копии восстановления."
        )
    ddl: str = connection.exec_driver_sql(
        "SELECT sql FROM sqlite_master WHERE type='table' AND name='entities'"
    ).scalar_one()
    old_kinds = (
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
    )
    pattern = (
        r"(?i:kind\s+IN)\s*\(\s*"
        + r"\s*,\s*".join(re.escape("'" + kind + "'") for kind in old_kinds)
        + r"\s*\)"
    )
    widened, changes = re.subn(
        pattern,
        "kind IN ("
        + ",".join("'" + kind + "'" for kind in (*old_kinds, "planning_scenario"))
        + ")",
        ddl,
    )
    if changes != 1:
        raise RuntimeError("Ограничение типов entities отличается от ожидаемого.")
    staged, changes = re.subn(
        r'\ACREATE TABLE (?:"entities"|entities)(?=\s*\()',
        'CREATE TABLE "entities_0015"',
        widened,
        count=1,
        flags=re.I,
    )
    if changes != 1:
        raise RuntimeError("Определение entities отличается от ожидаемого.")
    object_rows = connection.exec_driver_sql(
        "SELECT type,name,tbl_name,sql FROM sqlite_master "
        "WHERE type IN ('trigger','index','view') AND sql IS NOT NULL ORDER BY rowid"
    ).fetchall()
    rows = [(str(kind), str(name), str(table), str(sql)) for kind, name, table, sql in object_rows]
    reference = re.compile(r"\bentities\b", re.I)
    if any(kind == "view" and reference.search(sql) for kind, _, _, sql in rows):
        raise RuntimeError("Неизвестное представление ссылается на entities; переход остановлен.")
    triggers = [
        (name, sql) for kind, name, _, sql in rows if kind == "trigger" and reference.search(sql)
    ]
    indexes = [
        (name, sql) for kind, name, table, sql in rows if kind == "index" and table == "entities"
    ]
    payload = next((sql for name, sql in triggers if name == "revision_payload_valid"), None)
    if payload is None:
        raise RuntimeError("Не найдена защита состава версии.")
    suffix = ") BEGIN SELECT RAISE(ABORT, 'missing typed payload'); END"
    if payload.count(suffix) != 1 or any(
        name not in payload
        for name in (
            "category_rule_revisions",
            "deposit_scenario_revisions",
            "bonus_program_revisions",
            "bonus_entry_revisions",
        )
    ):
        raise RuntimeError("Защита состава версии отличается от ожидаемой.")
    new_payload = payload.replace(
        suffix,
        " OR EXISTS (SELECT 1 FROM entities e JOIN planning_scenario_revisions p "
        "ON p.revision_id=NEW.id WHERE e.id=NEW.entity_id AND e.kind='planning_scenario')" + suffix,
    )
    columns = ",".join(
        _quoted(str(row[1])) for row in connection.exec_driver_sql("PRAGMA table_info(entities)")
    )
    op.execute(staged)
    op.execute(f"INSERT INTO entities_0015 ({columns}) SELECT {columns} FROM entities")
    different: int = connection.exec_driver_sql(
        "SELECT EXISTS(SELECT * FROM entities EXCEPT SELECT * FROM entities_0015)"
        "+ EXISTS(SELECT * FROM entities_0015 EXCEPT SELECT * FROM entities)"
    ).scalar_one()
    if (
        different
        or connection.exec_driver_sql("SELECT count(*) FROM entities_0015").scalar_one() != count
    ):
        raise RuntimeError("Перенос entities не подтверждён; переход отменён.")
    for name, _ in triggers:
        op.execute("DROP TRIGGER " + _quoted(name))
    op.execute("DROP TABLE entities")
    op.execute("ALTER TABLE entities_0015 RENAME TO entities")
    for _, sql in indexes:
        op.execute(sql)
    op.execute("""CREATE TABLE planning_scenario_revisions (
        revision_id TEXT PRIMARY KEY REFERENCES entity_revisions(id),
        scenario_kind TEXT NOT NULL CHECK(scenario_kind IN ('budget','savings_goal')),
        name TEXT NOT NULL CHECK(length(trim(name)) BETWEEN 1 AND 120),
        name_key TEXT NOT NULL CHECK(length(name_key) BETWEEN 1 AND 240),
        scenario_json TEXT NOT NULL CHECK(length(CAST(scenario_json AS BLOB))
            BETWEEN 1 AND 4194304),
        CHECK(CASE WHEN json_valid(scenario_json) THEN COALESCE(
            json_type(scenario_json,'$.format')='integer'
            AND json_extract(scenario_json,'$.format')=1
            AND json_type(scenario_json,'$.kind')='text'
            AND json_extract(scenario_json,'$.kind')=scenario_kind
            AND json_type(scenario_json,'$.currency')='object'
            AND json_type(scenario_json,'$.currency.code')='text'
            AND length(json_extract(scenario_json,'$.currency.code'))=3
            AND json_extract(scenario_json,'$.currency.code') NOT GLOB '*[^A-Z]*'
            AND json_type(scenario_json,'$.currency.exponent')='integer'
            AND json_extract(scenario_json,'$.currency.exponent') BETWEEN 0 AND 8
            AND ((scenario_kind='budget'
                AND json_extract(scenario_json,'$.algorithm')='budget-postings-v1'
                AND json_extract(scenario_json,'$.period_mode') IN ('month','dates')
                AND json_type(scenario_json,'$.start')='text'
                AND length(json_extract(scenario_json,'$.start'))=10
                AND json_type(scenario_json,'$.end')='text'
                AND length(json_extract(scenario_json,'$.end'))=10
                AND json_extract(scenario_json,'$.start')<=json_extract(scenario_json,'$.end')
                AND json_type(scenario_json,'$.limit_minor')='integer'
                AND typeof(json_extract(scenario_json,'$.limit_minor'))='integer'
                AND json_extract(scenario_json,'$.limit_minor')>=0
                AND json_type(scenario_json,'$.account_ids') IN ('null','array')
                AND json_type(scenario_json,'$.category_ids') IN ('null','array')
                AND CASE WHEN json_type(scenario_json,'$.account_ids')='array'
                    THEN json_array_length(scenario_json,'$.account_ids')<=10000 ELSE 1 END
                AND CASE WHEN json_type(scenario_json,'$.category_ids')='array'
                    THEN json_array_length(scenario_json,'$.category_ids')<=10000 ELSE 1 END)
            OR (scenario_kind='savings_goal'
                AND json_extract(scenario_json,'$.algorithm')='monthly-contribution-v1'
                AND json_type(scenario_json,'$.as_of')='text'
                AND length(json_extract(scenario_json,'$.as_of'))=10
                AND json_type(scenario_json,'$.current_minor')='integer'
                AND typeof(json_extract(scenario_json,'$.current_minor'))='integer'
                AND json_extract(scenario_json,'$.current_minor')>=0
                AND json_type(scenario_json,'$.target_minor')='integer'
                AND typeof(json_extract(scenario_json,'$.target_minor'))='integer'
                AND json_extract(scenario_json,'$.target_minor')>0
                AND json_type(scenario_json,'$.monthly_minor')='integer'
                AND typeof(json_extract(scenario_json,'$.monthly_minor'))='integer'
                AND json_extract(scenario_json,'$.monthly_minor')>=0
                AND (json_type(scenario_json,'$.deadline')='null'
                    OR (json_type(scenario_json,'$.deadline')='text'
                        AND length(json_extract(scenario_json,'$.deadline'))=10
                        AND json_extract(scenario_json,'$.deadline')>=
                            json_extract(scenario_json,'$.as_of'))))),0) ELSE 0 END)
    ) STRICT""")
    op.execute(
        "CREATE INDEX ix_planning_scenario_name ON planning_scenario_revisions "
        "(scenario_kind,name_key,revision_id)"
    )
    for name, sql in triggers:
        op.execute(new_payload if name == "revision_payload_valid" else sql)
    for action in ("UPDATE", "DELETE"):
        op.execute(f"""CREATE TRIGGER planning_scenario_no_{action.lower()} BEFORE {action}
            ON planning_scenario_revisions WHEN EXISTS (SELECT 1 FROM entity_revisions r
            WHERE r.id=OLD.revision_id AND r.sealed=1)
            BEGIN SELECT RAISE(ABORT, 'revision is sealed'); END""")
    op.execute("""CREATE TRIGGER planning_scenario_no_insert_sealed BEFORE INSERT
        ON planning_scenario_revisions
        WHEN EXISTS (SELECT 1 FROM entity_revisions r WHERE r.id=NEW.revision_id AND
            r.sealed=1)
        BEGIN SELECT RAISE(ABORT, 'revision is sealed'); END""")
    op.execute("""CREATE TRIGGER planning_scenario_kind_valid BEFORE INSERT
        ON planning_scenario_revisions
        WHEN NOT EXISTS (SELECT 1 FROM entity_revisions r JOIN entities e ON
            e.id=r.entity_id
        WHERE r.id=NEW.revision_id AND e.kind='planning_scenario')
        BEGIN SELECT RAISE(ABORT, 'invalid planning scenario kind'); END""")
    op.execute("""CREATE TRIGGER planning_scenario_parent_valid BEFORE UPDATE OF sealed
        ON entity_revisions
        WHEN NEW.sealed=1 AND OLD.sealed=0 AND EXISTS (
        SELECT 1 FROM entities WHERE id=NEW.entity_id AND kind='planning_scenario') AND (
        (SELECT count(*) FROM revision_parents WHERE revision_id=NEW.id)>1
        OR EXISTS (SELECT 1 FROM entities e WHERE e.id=NEW.entity_id AND
            e.accepted_revision_id IS NULL
            AND (NEW.is_deleted=1 OR EXISTS (SELECT 1 FROM revision_parents WHERE
            revision_id=NEW.id)))
        OR EXISTS (SELECT 1 FROM entities e WHERE e.id=NEW.entity_id AND
            e.accepted_revision_id IS NOT NULL
            AND NOT EXISTS (SELECT 1 FROM revision_parents WHERE revision_id=NEW.id AND
            parent_id=e.accepted_revision_id)))
        BEGIN SELECT RAISE(ABORT, 'invalid planning scenario parent'); END""")
    op.execute("""CREATE TRIGGER planning_scenario_name_unique BEFORE UPDATE OF sealed
        ON entity_revisions
        WHEN NEW.sealed=1 AND OLD.sealed=0 AND NEW.is_deleted=0 AND EXISTS (
        SELECT 1 FROM entities own JOIN planning_scenario_revisions value ON
            value.revision_id=NEW.id
        JOIN entities other ON other.book_id=own.book_id AND
            other.kind='planning_scenario' AND other.id<>own.id
        JOIN entity_revisions r ON r.id=other.accepted_revision_id AND r.is_deleted=0
        JOIN planning_scenario_revisions previous ON previous.revision_id=r.id
        WHERE own.id=NEW.entity_id AND own.kind='planning_scenario' AND
            previous.name_key=value.name_key AND previous.scenario_kind=value.scenario_kind)
        BEGIN SELECT RAISE(ABORT, 'active planning scenario name is not unique'); END""")
    op.execute("""CREATE TRIGGER planning_scenario_count_valid BEFORE UPDATE OF sealed
        ON entity_revisions
        WHEN NEW.sealed=1 AND OLD.sealed=0 AND NEW.is_deleted=0 AND EXISTS (
        SELECT 1 FROM entities own JOIN planning_scenario_revisions value ON value.revision_id=NEW.id
        WHERE own.id=NEW.entity_id AND own.kind='planning_scenario'
        AND (SELECT count(*) FROM entities other JOIN entity_revisions r ON
            r.id=other.accepted_revision_id
            JOIN planning_scenario_revisions previous ON previous.revision_id=r.id
            WHERE previous.scenario_kind=value.scenario_kind AND other.book_id=own.book_id AND other.kind='planning_scenario' AND
            other.id<>own.id AND r.is_deleted=0)>=1000)
        BEGIN SELECT RAISE(ABORT, 'too many active planning scenarios'); END""")

    op.execute("""CREATE TRIGGER planning_scenario_type_immutable BEFORE UPDATE OF sealed
        ON entity_revisions
        WHEN NEW.sealed=1 AND OLD.sealed=0 AND EXISTS(
            SELECT 1 FROM planning_scenario_revisions value
            JOIN entity_revisions old ON old.entity_id=NEW.entity_id AND old.id<>NEW.id
            JOIN planning_scenario_revisions previous ON previous.revision_id=old.id
            WHERE value.revision_id=NEW.id
                AND previous.scenario_kind<>value.scenario_kind)
        BEGIN SELECT RAISE(ABORT, 'planning scenario type is immutable'); END""")
    for field, kind, table, predicate in (
        (
            "account_ids",
            "account",
            "account_revisions",
            "p.currency=json_extract(value.scenario_json,'$.currency.code') "
            "AND EXISTS(SELECT 1 FROM currencies c WHERE c.code=p.currency "
            "AND c.exponent=json_extract(value.scenario_json,'$.currency.exponent'))",
        ),
        ("category_ids", "category", "category_revisions", "p.kind='expense'"),
    ):
        op.execute(f"""CREATE TRIGGER planning_scope_{field} BEFORE UPDATE OF sealed
            ON entity_revisions
            WHEN NEW.sealed=1 AND OLD.sealed=0 AND EXISTS(
                SELECT 1 FROM planning_scenario_revisions value
                WHERE value.revision_id=NEW.id AND value.scenario_kind='budget'
                    AND json_type(value.scenario_json,'$.{field}')='array'
                    AND (json_array_length(value.scenario_json,'$.{field}')<>
                        (SELECT count(DISTINCT item.value)
                            FROM json_each(value.scenario_json,'$.{field}') item)
                    OR EXISTS(SELECT 1 FROM json_each(value.scenario_json,'$.{field}') chosen
                        JOIN entities own ON own.id=NEW.entity_id
                        WHERE chosen.type<>'text' OR NOT EXISTS(
                            SELECT 1 FROM entities target WHERE target.id=chosen.value
                                AND target.book_id=own.book_id AND target.kind='{kind}'
                                AND (NEW.is_deleted=1 OR EXISTS(
                                    SELECT 1 FROM entity_revisions accepted
                                    JOIN {table} p ON p.revision_id=accepted.id
                                    WHERE accepted.id=target.accepted_revision_id
                                        AND accepted.sealed=1 AND accepted.is_deleted=0
                                        AND {predicate}))))))
            BEGIN SELECT RAISE(ABORT, 'invalid planning scope'); END""")


def downgrade() -> None:
    raise RuntimeError("Понижение схемы запрещено; используйте исходную книгу.")
