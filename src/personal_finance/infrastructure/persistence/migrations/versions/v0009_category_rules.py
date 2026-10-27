"""Неизменяемые правила категоризации; прежние финансовые версии сохраняются."""

import sqlalchemy as sa
from alembic import op

revision = "0009"
down_revision = "0008"
branch_labels = None
depends_on = None


def upgrade() -> None:
    connection = op.get_bind()
    # Batch-пересоздание entities затрагивает ссылающиеся триггеры. Сохраняем
    # точные определения всех прежних защит и возвращаем их в той же транзакции.
    triggers: list[tuple[str, str]] = []
    for name, sql in connection.exec_driver_sql(
        "SELECT name,sql FROM sqlite_master WHERE type='trigger' ORDER BY name"
    ):
        if not isinstance(name, str) or not isinstance(sql, str):
            raise RuntimeError("Некорректные определения защит исходной схемы.")
        triggers.append((name, sql))
    for name, _ in triggers:
        op.execute('DROP TRIGGER "' + name.replace('"', '""') + '"')
    with op.batch_alter_table(
        "entities", recreate="always", table_kwargs={"sqlite_strict": True}
    ) as batch:
        batch.drop_constraint("ck_entity_kind", type_="check")
        batch.create_check_constraint(
            "ck_entity_kind",
            "kind IN ('institution','account','category','operation','period',"
            "'tag','saved_filter','category_rule')",
        )
    op.create_table(
        "category_rule_revisions",
        sa.Column("revision_id", sa.Text, sa.ForeignKey("entity_revisions.id"), primary_key=True),
        sa.Column("name", sa.Text, nullable=False),
        sa.Column("name_key", sa.Text, nullable=False),
        sa.Column("operation_kind", sa.Text, nullable=False),
        sa.Column("contains_text", sa.Text, nullable=False),
        sa.Column("category_id", sa.Text, sa.ForeignKey("entities.id"), nullable=False),
        sa.Column("priority", sa.Integer, nullable=False),
        sa.Column("account_id", sa.Text, sa.ForeignKey("entities.id")),
        sa.CheckConstraint("length(trim(name)) BETWEEN 1 AND 120", name="ck_rule_name"),
        sa.CheckConstraint("length(trim(contains_text)) BETWEEN 1 AND 200", name="ck_rule_text"),
        sa.CheckConstraint("operation_kind IN ('income','expense')", name="ck_rule_kind"),
        sa.CheckConstraint("priority BETWEEN 0 AND 999", name="ck_rule_priority"),
        sqlite_strict=True,
    )
    op.create_index("ix_category_rule_name", "category_rule_revisions", ["name_key", "revision_id"])
    for name, sql in triggers:
        if name != "revision_payload_valid":
            op.execute(sql)
    tables = {
        "institution": "institution_revisions",
        "account": "account_revisions",
        "category": "category_revisions",
        "operation": "operation_revisions",
        "period": "period_revisions",
        "tag": "tag_revisions",
        "saved_filter": "saved_filter_revisions",
        "category_rule": "category_rule_revisions",
    }
    choices = " OR ".join(
        f"EXISTS (SELECT 1 FROM entities e JOIN {table} p ON p.revision_id=NEW.id "
        f"WHERE e.id=NEW.entity_id AND e.kind='{kind}')"
        for kind, table in tables.items()
    )
    op.execute(
        "CREATE TRIGGER revision_payload_valid BEFORE UPDATE OF sealed ON entity_revisions "
        "WHEN NEW.sealed=1 AND OLD.sealed=0 AND NOT (" + choices + ") "
        "BEGIN SELECT RAISE(ABORT, 'missing typed payload'); END"
    )
    for action in ("UPDATE", "DELETE"):
        op.execute(f"""CREATE TRIGGER category_rule_no_{action.lower()} BEFORE {action}
            ON category_rule_revisions WHEN EXISTS (SELECT 1 FROM entity_revisions r
            WHERE r.id=OLD.revision_id AND r.sealed=1)
            BEGIN SELECT RAISE(ABORT, 'revision is sealed'); END""")
    op.execute("""CREATE TRIGGER category_rule_no_insert_sealed BEFORE INSERT ON
    category_rule_revisions
        WHEN EXISTS (SELECT 1 FROM entity_revisions r WHERE r.id=NEW.revision_id AND r.sealed=1)
        BEGIN SELECT RAISE(ABORT, 'revision is sealed'); END""")
    op.execute("""CREATE TRIGGER category_rule_parent_valid BEFORE UPDATE OF sealed ON
    entity_revisions
        WHEN NEW.sealed=1 AND OLD.sealed=0 AND EXISTS (
        SELECT 1 FROM entities WHERE id=NEW.entity_id AND kind='category_rule') AND (
        (SELECT count(*) FROM revision_parents WHERE revision_id=NEW.id)>1
        OR EXISTS (SELECT 1 FROM entities e WHERE e.id=NEW.entity_id AND e.accepted_revision_id
        IS NULL
            AND (NEW.is_deleted=1 OR EXISTS (SELECT 1 FROM revision_parents WHERE
            revision_id=NEW.id)))
        OR EXISTS (SELECT 1 FROM entities e WHERE e.id=NEW.entity_id AND e.accepted_revision_id
        IS NOT NULL
            AND NOT EXISTS (SELECT 1 FROM revision_parents WHERE revision_id=NEW.id AND
            parent_id=e.accepted_revision_id)))
        BEGIN SELECT RAISE(ABORT, 'invalid category rule parent'); END""")
    op.execute("""CREATE TRIGGER category_rule_references_valid BEFORE UPDATE OF sealed ON
    entity_revisions
        WHEN NEW.sealed=1 AND OLD.sealed=0 AND EXISTS (
        SELECT 1 FROM entities own JOIN category_rule_revisions value ON value.revision_id=NEW.id
        WHERE own.id=NEW.entity_id AND own.kind='category_rule' AND (
            NOT EXISTS (SELECT 1 FROM entities c JOIN entity_revisions r ON
            r.id=c.accepted_revision_id
                JOIN category_revisions p ON p.revision_id=r.id WHERE c.id=value.category_id
                AND c.book_id=own.book_id AND c.kind='category' AND r.sealed=1 AND
                p.kind=value.operation_kind)
            OR (value.account_id IS NOT NULL AND NOT EXISTS (
                SELECT 1 FROM entities a JOIN entity_revisions r ON r.id=a.accepted_revision_id
                WHERE a.id=value.account_id AND a.book_id=own.book_id AND a.kind='account' AND
                r.sealed=1))))
        BEGIN SELECT RAISE(ABORT, 'invalid category rule references'); END""")
    op.execute("""CREATE TRIGGER category_rule_name_unique BEFORE UPDATE OF sealed ON
    entity_revisions
        WHEN NEW.sealed=1 AND OLD.sealed=0 AND NEW.is_deleted=0 AND EXISTS (
        SELECT 1 FROM entities own JOIN category_rule_revisions value ON value.revision_id=NEW.id
        JOIN entities other ON other.book_id=own.book_id AND other.kind='category_rule' AND
        other.id<>own.id
        JOIN entity_revisions r ON r.id=other.accepted_revision_id AND r.is_deleted=0
        JOIN category_rule_revisions previous ON previous.revision_id=r.id
        WHERE own.id=NEW.entity_id AND own.kind='category_rule' AND
        previous.name_key=value.name_key)
        BEGIN SELECT RAISE(ABORT, 'active rule name is not unique'); END""")
    op.execute("""CREATE TRIGGER category_rule_count_valid BEFORE UPDATE OF sealed ON
    entity_revisions
        WHEN NEW.sealed=1 AND OLD.sealed=0 AND NEW.is_deleted=0 AND EXISTS (
        SELECT 1 FROM entities own WHERE own.id=NEW.entity_id AND own.kind='category_rule'
        AND (SELECT count(*) FROM entities other JOIN entity_revisions r ON
        r.id=other.accepted_revision_id
            WHERE other.book_id=own.book_id AND other.kind='category_rule' AND other.id<>own.id
            AND r.is_deleted=0)>=1000)
        BEGIN SELECT RAISE(ABORT, 'too many active category rules'); END""")


def downgrade() -> None:
    raise RuntimeError("Понижение схемы запрещено; используйте исходную книгу.")
