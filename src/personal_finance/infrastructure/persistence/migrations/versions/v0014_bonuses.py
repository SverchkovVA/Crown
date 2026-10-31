"""Схема 0014: отдельные программы и записи бонусов; денежный журнал прежний."""

import re

from alembic import op

revision = "0014"
down_revision = "0013"
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
        r"\bkind\s+IN\s*\([^)]*'deposit_scenario'\s*\)",
        "kind IN ('institution','account','category','operation','period',"
        "'tag','saved_filter','category_rule','deposit_scenario','bonus_program','bonus_entry')",
        ddl,
        flags=re.I,
    )
    if changes != 1:
        raise RuntimeError("Ограничение типов entities отличается от ожидаемого.")
    staged, changes = re.subn(
        r'\ACREATE TABLE (?:"entities"|entities)(?=\s*\()',
        'CREATE TABLE "entities_0014"',
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
        " OR EXISTS (SELECT 1 FROM entities e JOIN bonus_program_revisions p "
        "ON p.revision_id=NEW.id WHERE e.id=NEW.entity_id AND e.kind='bonus_program')"
        " OR EXISTS (SELECT 1 FROM entities e JOIN bonus_entry_revisions p "
        "ON p.revision_id=NEW.id WHERE e.id=NEW.entity_id AND e.kind='bonus_entry')" + suffix,
    )
    columns = ",".join(
        _quoted(str(row[1])) for row in connection.exec_driver_sql("PRAGMA table_info(entities)")
    )
    op.execute(staged)
    op.execute(f"INSERT INTO entities_0014 ({columns}) SELECT {columns} FROM entities")
    different: int = connection.exec_driver_sql(
        "SELECT EXISTS(SELECT * FROM entities EXCEPT SELECT * FROM entities_0014)"
        "+ EXISTS(SELECT * FROM entities_0014 EXCEPT SELECT * FROM entities)"
    ).scalar_one()
    if (
        different
        or connection.exec_driver_sql("SELECT count(*) FROM entities_0014").scalar_one() != count
    ):
        raise RuntimeError("Перенос entities не подтверждён; переход отменён.")
    for name, _ in triggers:
        op.execute("DROP TRIGGER " + _quoted(name))
    op.execute("DROP TABLE entities")
    op.execute("ALTER TABLE entities_0014 RENAME TO entities")
    for _, sql in indexes:
        op.execute(sql)
    op.execute("""CREATE TABLE bonus_program_revisions (
        revision_id TEXT PRIMARY KEY REFERENCES entity_revisions(id),
        name TEXT NOT NULL CHECK(length(trim(name)) BETWEEN 1 AND 120),
        name_key TEXT NOT NULL CHECK(length(name_key) BETWEEN 1 AND 240)
    ) STRICT""")
    op.execute("""CREATE TABLE bonus_entry_revisions (
        revision_id TEXT PRIMARY KEY REFERENCES entity_revisions(id),
        program_id TEXT NOT NULL REFERENCES entities(id),
        occurred_on TEXT NOT NULL CHECK(length(occurred_on)=10),
        kind TEXT NOT NULL CHECK(kind IN ('opening','accrual','spending','expiry','adjustment')),
        units_minor INTEGER NOT NULL CHECK(units_minor<>0 AND
        abs(units_minor)<=9223372036854775807),
        category_id TEXT REFERENCES entities(id),
        operation_id TEXT REFERENCES entities(id),
        note TEXT NOT NULL CHECK(length(note)<=500),
        source_key TEXT CHECK(source_key IS NULL OR
            (length(source_key)=64 AND source_key NOT GLOB '*[^0-9a-f]*')),
        bank_category TEXT NOT NULL CHECK(length(bank_category)<=120),
        CHECK((kind IN ('opening','accrual') AND units_minor>0)
            OR (kind IN ('spending','expiry') AND units_minor<0) OR kind='adjustment')
    ) STRICT""")
    op.execute("CREATE INDEX ix_bonus_program_name ON bonus_program_revisions(name_key)")
    op.execute(
        "CREATE INDEX ix_bonus_entry_program ON bonus_entry_revisions(program_id,occurred_on)"
    )
    op.execute("CREATE INDEX ix_bonus_entry_source ON bonus_entry_revisions(source_key)")
    for name, sql in triggers:
        op.execute(new_payload if name == "revision_payload_valid" else sql)
    for kind in ("bonus_program", "bonus_entry"):
        table = kind + "_revisions"
        for action in ("UPDATE", "DELETE"):
            op.execute(f"""CREATE TRIGGER {kind}_no_{action.lower()} BEFORE {action} ON {table}
                WHEN EXISTS(SELECT 1 FROM entity_revisions WHERE id=OLD.revision_id AND sealed=1)
                BEGIN SELECT RAISE(ABORT, 'revision is sealed'); END""")
        op.execute(f"""CREATE TRIGGER {kind}_no_insert_sealed BEFORE INSERT ON {table}
            WHEN EXISTS(SELECT 1 FROM entity_revisions WHERE id=NEW.revision_id AND sealed=1)
            BEGIN SELECT RAISE(ABORT, 'revision is sealed'); END""")
        op.execute(f"""CREATE TRIGGER {kind}_kind_valid BEFORE INSERT ON {table}
            WHEN NOT EXISTS(SELECT 1 FROM entity_revisions r JOIN entities e ON e.id=r.entity_id
                WHERE r.id=NEW.revision_id AND e.kind='{kind}')
            BEGIN SELECT RAISE(ABORT, 'invalid bonus payload kind'); END""")
    op.execute("""CREATE TRIGGER bonus_parent_valid BEFORE UPDATE OF sealed ON entity_revisions
        WHEN NEW.sealed=1 AND OLD.sealed=0 AND EXISTS
            (SELECT 1 FROM entities WHERE id=NEW.entity_id AND kind IN
        ('bonus_program','bonus_entry'))
        AND ((SELECT count(*) FROM revision_parents WHERE revision_id=NEW.id)>1
        OR EXISTS(SELECT 1 FROM entities WHERE id=NEW.entity_id AND accepted_revision_id IS NULL
            AND (NEW.is_deleted=1 OR EXISTS(SELECT 1 FROM revision_parents WHERE
        revision_id=NEW.id)))
        OR EXISTS(SELECT 1 FROM entities e WHERE e.id=NEW.entity_id AND accepted_revision_id IS
        NOT NULL
            AND NOT EXISTS(SELECT 1 FROM revision_parents WHERE revision_id=NEW.id
                AND parent_id=e.accepted_revision_id)))
        BEGIN SELECT RAISE(ABORT, 'invalid bonus parent'); END""")
    op.execute("""CREATE TRIGGER bonus_program_name_unique BEFORE UPDATE OF sealed ON
        entity_revisions
        WHEN NEW.sealed=1 AND OLD.sealed=0 AND NEW.is_deleted=0 AND EXISTS(
            SELECT 1 FROM entities own JOIN bonus_program_revisions v ON v.revision_id=NEW.id
            JOIN entities other ON other.book_id=own.book_id AND other.kind='bonus_program'
                AND other.id<>own.id
            JOIN entity_revisions r ON r.id=other.accepted_revision_id AND r.is_deleted=0
            JOIN bonus_program_revisions p ON p.revision_id=r.id
            WHERE own.id=NEW.entity_id AND own.kind='bonus_program' AND p.name_key=v.name_key)
        BEGIN SELECT RAISE(ABORT, 'duplicate bonus program name'); END""")
    op.execute("""CREATE TRIGGER bonus_entry_links_valid BEFORE INSERT ON bonus_entry_revisions
        WHEN NOT EXISTS(SELECT 1 FROM entity_revisions r JOIN entities own ON own.id=r.entity_id
            JOIN entities p ON p.id=NEW.program_id AND p.book_id=own.book_id AND
        p.kind='bonus_program'
            WHERE r.id=NEW.revision_id)
        OR (NEW.category_id IS NOT NULL AND NOT EXISTS(
            SELECT 1 FROM entity_revisions r JOIN entities own ON own.id=r.entity_id
            JOIN entities c ON c.id=NEW.category_id AND c.book_id=own.book_id AND c.kind='category'
            JOIN category_revisions cv ON cv.revision_id=c.accepted_revision_id AND
        cv.kind='expense'
            WHERE r.id=NEW.revision_id))
        OR (NEW.operation_id IS NOT NULL AND NOT EXISTS(
            SELECT 1 FROM entity_revisions r JOIN entities own ON own.id=r.entity_id
            JOIN entities o ON o.id=NEW.operation_id AND o.book_id=own.book_id AND
        o.kind='operation'
            WHERE r.id=NEW.revision_id))
        BEGIN SELECT RAISE(ABORT, 'invalid bonus links'); END""")
    op.execute("""CREATE TRIGGER bonus_source_unique BEFORE UPDATE OF sealed ON entity_revisions
        WHEN NEW.sealed=1 AND OLD.sealed=0 AND EXISTS(
            SELECT 1 FROM entities own JOIN bonus_entry_revisions v ON v.revision_id=NEW.id
            JOIN entities other ON other.book_id=own.book_id AND other.kind='bonus_entry'
                AND other.id<>own.id
            JOIN bonus_entry_revisions p ON p.revision_id=other.accepted_revision_id
            WHERE own.id=NEW.entity_id AND own.kind='bonus_entry' AND v.source_key IS NOT NULL
                AND p.source_key=v.source_key)
        BEGIN SELECT RAISE(ABORT, 'bonus source already recorded'); END""")


def downgrade() -> None:
    raise RuntimeError("Понижение схемы запрещено; используйте исходную книгу.")
