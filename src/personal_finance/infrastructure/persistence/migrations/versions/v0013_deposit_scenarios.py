"""Схема 0013: версии условий сценариев; прежняя финансовая история сохраняется."""

import re

from alembic import op

revision = "0013"
down_revision = "0012"
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
    widened, changes = re.subn(
        r"\bkind\s+IN\s*\(\s*'institution'\s*,\s*'account'\s*,\s*"
        r"'category'\s*,\s*'operation'\s*,\s*'period'\s*,\s*'tag'\s*,\s*"
        r"'saved_filter'\s*,\s*'category_rule'\s*\)",
        "kind IN ('institution','account','category','operation','period',"
        "'tag','saved_filter','category_rule','deposit_scenario')",
        ddl,
        flags=re.I,
    )
    if changes != 1:
        raise RuntimeError("Ограничение типов entities отличается от ожидаемого.")
    staged, changes = re.subn(
        r'\ACREATE TABLE (?:"entities"|entities)(?=\s*\()',
        'CREATE TABLE "entities_0013"',
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
    if payload.count(suffix) != 1 or "category_rule_revisions" not in payload:
        raise RuntimeError("Защита состава версии отличается от ожидаемой.")
    new_payload = payload.replace(
        suffix,
        " OR EXISTS (SELECT 1 FROM entities e JOIN deposit_scenario_revisions p "
        "ON p.revision_id=NEW.id WHERE e.id=NEW.entity_id AND e.kind='deposit_scenario')" + suffix,
    )
    columns = ",".join(
        _quoted(str(row[1])) for row in connection.exec_driver_sql("PRAGMA table_info(entities)")
    )
    op.execute(staged)
    op.execute(f"INSERT INTO entities_0013 ({columns}) SELECT {columns} FROM entities")
    different: int = connection.exec_driver_sql(
        "SELECT EXISTS(SELECT * FROM entities EXCEPT SELECT * FROM entities_0013)"
        "+ EXISTS(SELECT * FROM entities_0013 EXCEPT SELECT * FROM entities)"
    ).scalar_one()
    if (
        different
        or connection.exec_driver_sql("SELECT count(*) FROM entities_0013").scalar_one() != count
    ):
        raise RuntimeError("Перенос entities не подтверждён; переход отменён.")
    for name, _ in triggers:
        op.execute("DROP TRIGGER " + _quoted(name))
    op.execute("DROP TABLE entities")
    op.execute("ALTER TABLE entities_0013 RENAME TO entities")
    for _, sql in indexes:
        op.execute(sql)
    op.execute("""CREATE TABLE deposit_scenario_revisions (
        revision_id TEXT PRIMARY KEY REFERENCES entity_revisions(id),
        name TEXT NOT NULL CHECK(length(trim(name)) BETWEEN 1 AND 120),
        name_key TEXT NOT NULL,
        scenario_json TEXT NOT NULL CHECK(length(CAST(scenario_json AS BLOB)) BETWEEN 1
            AND 4194304)
    ) STRICT""")
    op.execute(
        "CREATE INDEX ix_deposit_scenario_name ON deposit_scenario_revisions(name_key,revision_id)"
    )
    for name, sql in triggers:
        op.execute(new_payload if name == "revision_payload_valid" else sql)
    for action in ("UPDATE", "DELETE"):
        op.execute(f"""CREATE TRIGGER deposit_scenario_no_{action.lower()} BEFORE {action}
            ON deposit_scenario_revisions WHEN EXISTS (SELECT 1 FROM entity_revisions r
            WHERE r.id=OLD.revision_id AND r.sealed=1)
            BEGIN SELECT RAISE(ABORT, 'revision is sealed'); END""")
    op.execute("""CREATE TRIGGER deposit_scenario_no_insert_sealed BEFORE INSERT
        ON deposit_scenario_revisions
        WHEN EXISTS (SELECT 1 FROM entity_revisions r WHERE r.id=NEW.revision_id AND
            r.sealed=1)
        BEGIN SELECT RAISE(ABORT, 'revision is sealed'); END""")
    op.execute("""CREATE TRIGGER deposit_scenario_kind_valid BEFORE INSERT
        ON deposit_scenario_revisions
        WHEN NOT EXISTS (SELECT 1 FROM entity_revisions r JOIN entities e ON
            e.id=r.entity_id
        WHERE r.id=NEW.revision_id AND e.kind='deposit_scenario')
        BEGIN SELECT RAISE(ABORT, 'invalid deposit scenario kind'); END""")
    op.execute("""CREATE TRIGGER deposit_scenario_parent_valid BEFORE UPDATE OF sealed
        ON entity_revisions
        WHEN NEW.sealed=1 AND OLD.sealed=0 AND EXISTS (
        SELECT 1 FROM entities WHERE id=NEW.entity_id AND kind='deposit_scenario') AND (
        (SELECT count(*) FROM revision_parents WHERE revision_id=NEW.id)>1
        OR EXISTS (SELECT 1 FROM entities e WHERE e.id=NEW.entity_id AND
            e.accepted_revision_id IS NULL
            AND (NEW.is_deleted=1 OR EXISTS (SELECT 1 FROM revision_parents WHERE
            revision_id=NEW.id)))
        OR EXISTS (SELECT 1 FROM entities e WHERE e.id=NEW.entity_id AND
            e.accepted_revision_id IS NOT NULL
            AND NOT EXISTS (SELECT 1 FROM revision_parents WHERE revision_id=NEW.id AND
            parent_id=e.accepted_revision_id)))
        BEGIN SELECT RAISE(ABORT, 'invalid deposit scenario parent'); END""")
    op.execute("""CREATE TRIGGER deposit_scenario_name_unique BEFORE UPDATE OF sealed
        ON entity_revisions
        WHEN NEW.sealed=1 AND OLD.sealed=0 AND NEW.is_deleted=0 AND EXISTS (
        SELECT 1 FROM entities own JOIN deposit_scenario_revisions value ON
            value.revision_id=NEW.id
        JOIN entities other ON other.book_id=own.book_id AND
            other.kind='deposit_scenario' AND other.id<>own.id
        JOIN entity_revisions r ON r.id=other.accepted_revision_id AND r.is_deleted=0
        JOIN deposit_scenario_revisions previous ON previous.revision_id=r.id
        WHERE own.id=NEW.entity_id AND own.kind='deposit_scenario' AND
            previous.name_key=value.name_key)
        BEGIN SELECT RAISE(ABORT, 'active deposit scenario name is not unique'); END""")
    op.execute("""CREATE TRIGGER deposit_scenario_count_valid BEFORE UPDATE OF sealed
        ON entity_revisions
        WHEN NEW.sealed=1 AND OLD.sealed=0 AND NEW.is_deleted=0 AND EXISTS (
        SELECT 1 FROM entities own WHERE own.id=NEW.entity_id AND
            own.kind='deposit_scenario'
        AND (SELECT count(*) FROM entities other JOIN entity_revisions r ON
            r.id=other.accepted_revision_id
            WHERE other.book_id=own.book_id AND other.kind='deposit_scenario' AND
            other.id<>own.id AND r.is_deleted=0)>=1000)
        BEGIN SELECT RAISE(ABORT, 'too many active deposit scenarios'); END""")


def downgrade() -> None:
    raise RuntimeError("Понижение схемы запрещено; используйте исходную книгу.")
