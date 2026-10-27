"""Теги операций и переносимые сохранённые фильтры."""

import sqlalchemy as sa
from alembic import op

revision = "0007"
down_revision = "0006"
branch_labels = None
depends_on = None

# Прежние определения защит неизменны; исходные миграции не редактируются.
_PRESERVED_TRIGGERS = (
    (
        "financial_parent_valid",
        """CREATE TRIGGER financial_parent_valid BEFORE UPDATE OF sealed ON entity_revisions
        WHEN NEW.sealed=1 AND OLD.sealed=0
        AND EXISTS (SELECT 1 FROM entities WHERE id=NEW.entity_id AND kind='operation')
        AND (
            (SELECT count(*) FROM revision_parents WHERE revision_id=NEW.id)>1
            OR EXISTS (SELECT 1 FROM entities e WHERE e.id=NEW.entity_id
                AND e.accepted_revision_id IS NULL
                AND EXISTS (SELECT 1 FROM revision_parents WHERE revision_id=NEW.id))
            OR EXISTS (SELECT 1 FROM entities e WHERE e.id=NEW.entity_id
                AND e.accepted_revision_id IS NOT NULL AND NOT EXISTS (
                    SELECT 1 FROM revision_parents p WHERE p.revision_id=NEW.id
                    AND p.parent_id=e.accepted_revision_id))
        )
        BEGIN SELECT RAISE(ABORT, 'invalid financial parent'); END""",
    ),
    (
        "financial_revision_balanced",
        """CREATE TRIGGER financial_revision_balanced BEFORE UPDATE OF sealed
        ON entity_revisions WHEN NEW.sealed=1 AND OLD.sealed=0
        AND EXISTS (SELECT 1 FROM entities WHERE id=NEW.entity_id AND kind='operation')
        AND (
            (SELECT count(*) FROM postings WHERE revision_id=NEW.id) NOT BETWEEN 2 AND 101
            OR EXISTS (
                SELECT l.currency FROM postings p JOIN ledger_accounts l
                ON l.id=p.ledger_account_id WHERE p.revision_id=NEW.id
                GROUP BY l.currency HAVING sum(p.amount_minor)<>0)
            OR EXISTS (
                SELECT 1 FROM postings p JOIN ledger_accounts l ON l.id=p.ledger_account_id
                JOIN entities e ON e.id=NEW.entity_id WHERE p.revision_id=NEW.id
                AND l.book_id<>e.book_id)
        )
        BEGIN SELECT RAISE(ABORT, 'invalid financial postings'); END""",
    ),
    (
        "financial_parts_valid",
        """CREATE TRIGGER financial_parts_valid BEFORE UPDATE OF sealed
        ON entity_revisions WHEN NEW.sealed=1 AND OLD.sealed=0
        AND EXISTS (SELECT 1 FROM entities WHERE id=NEW.entity_id AND kind='operation')
        AND EXISTS (
        SELECT 1 FROM operation_revisions o WHERE o.revision_id=NEW.id AND (
        (NOT EXISTS (SELECT 1 FROM operation_parts WHERE revision_id=NEW.id)
        AND ((o.kind IN ('income','expense','refund') AND o.category_id IS NULL)
        OR (SELECT count(*) FROM postings WHERE revision_id=NEW.id) NOT IN (2,3)))
        OR (EXISTS (SELECT 1 FROM operation_parts WHERE revision_id=NEW.id) AND (
        o.kind NOT IN ('income','expense','refund') OR o.category_id IS NOT NULL
        OR (SELECT count(*) FROM operation_parts WHERE revision_id=NEW.id)
        NOT BETWEEN 2 AND 100
        OR (SELECT sum(amount_minor) FROM operation_parts WHERE revision_id=NEW.id)
        <>o.amount_minor
        OR (SELECT min(position) FROM operation_parts WHERE revision_id=NEW.id)<>0
        OR (SELECT max(position)+1 FROM operation_parts WHERE revision_id=NEW.id)
        <>(SELECT count(*) FROM operation_parts WHERE revision_id=NEW.id)
        OR (SELECT count(*) FROM postings WHERE revision_id=NEW.id)
        <>(SELECT count(*)+1 FROM operation_parts WHERE revision_id=NEW.id)
        OR EXISTS (
        SELECT 1 FROM operation_parts part WHERE part.revision_id=NEW.id
        AND NOT EXISTS (
        SELECT 1 FROM entities e JOIN entity_revisions r
        ON r.id=e.accepted_revision_id JOIN category_revisions c
        ON c.revision_id=r.id JOIN entities operation
        ON operation.id=NEW.entity_id
        WHERE e.id=part.category_id AND e.book_id=operation.book_id
        AND e.kind='category' AND c.kind=CASE WHEN o.kind='refund'
        THEN 'expense' ELSE o.kind END
        )
        )
        ))
        )
        )
        BEGIN SELECT RAISE(ABORT, 'invalid financial parts'); END""",
    ),
    (
        "refund_source_valid",
        """CREATE TRIGGER refund_source_valid BEFORE UPDATE OF sealed
        ON entity_revisions WHEN NEW.sealed=1 AND OLD.sealed=0 AND EXISTS (
        SELECT 1 FROM operation_revisions value JOIN entities own ON own.id=NEW.entity_id
        WHERE value.revision_id=NEW.id AND value.kind='refund' AND NOT EXISTS (
            SELECT 1 FROM entities source JOIN entity_revisions r
            ON r.id=value.refund_of_revision_id JOIN operation_revisions p ON p.revision_id=r.id
            WHERE source.id=value.refund_of AND r.entity_id=source.id
            AND source.book_id=own.book_id AND source.kind='operation'
            AND r.sealed=1 AND r.is_deleted=0 AND p.kind='expense'
            AND p.currency=value.currency AND p.occurred_on<=value.occurred_on
        ))
        BEGIN SELECT RAISE(ABORT, 'invalid refund source'); END""",
    ),
    (
        "period_transition_valid",
        """CREATE TRIGGER period_transition_valid BEFORE UPDATE OF sealed
        ON entity_revisions WHEN NEW.sealed=1 AND OLD.sealed=0
        AND EXISTS (SELECT 1 FROM entities WHERE id=NEW.entity_id AND kind='period')
        AND (NEW.is_deleted<>0 OR EXISTS (
        SELECT 1 FROM period_revisions p JOIN entities own ON own.id=NEW.entity_id
        LEFT JOIN period_revisions previous ON previous.revision_id=own.accepted_revision_id
        WHERE p.revision_id=NEW.id AND (
            (own.accepted_revision_id IS NULL AND (p.is_closed<>1 OR EXISTS (
                SELECT 1 FROM revision_parents WHERE revision_id=NEW.id)))
            OR (own.accepted_revision_id IS NOT NULL AND (
                previous.month<>p.month OR previous.is_closed=p.is_closed
                OR (SELECT count(*) FROM revision_parents WHERE revision_id=NEW.id)<>1
                OR NOT EXISTS (SELECT 1 FROM revision_parents WHERE revision_id=NEW.id
                    AND parent_id=own.accepted_revision_id)))
            OR EXISTS (SELECT 1 FROM entities other JOIN period_revisions state
                ON state.revision_id=other.accepted_revision_id WHERE other.book_id=own.book_id
                AND other.kind='period' AND other.id<>own.id AND state.month=p.month)
        )))
        BEGIN SELECT RAISE(ABORT, 'invalid period transition'); END""",
    ),
    (
        "financial_period_open",
        """CREATE TRIGGER financial_period_open BEFORE UPDATE OF sealed ON entity_revisions
        WHEN NEW.sealed=1 AND OLD.sealed=0 AND EXISTS (
        SELECT 1 FROM operation_revisions p JOIN entities own ON own.id=NEW.entity_id
        JOIN entities period ON period.book_id=own.book_id AND period.kind='period'
        JOIN period_revisions state ON state.revision_id=period.accepted_revision_id
        WHERE p.revision_id=NEW.id AND own.kind='operation' AND state.is_closed=1 AND (
            state.month=substr(p.occurred_on,1,7) OR EXISTS (
                SELECT 1 FROM operation_revisions previous
                WHERE previous.revision_id=own.accepted_revision_id
                AND state.month=substr(previous.occurred_on,1,7))
        ))
        BEGIN SELECT RAISE(ABORT, 'financial period is closed'); END""",
    ),
)


def upgrade() -> None:
    op.execute("DROP TRIGGER revision_payload_valid")
    for name, _ in _PRESERVED_TRIGGERS:
        op.execute("DROP TRIGGER " + name)
    with op.batch_alter_table(
        "entities", recreate="always", table_kwargs={"sqlite_strict": True}
    ) as batch:
        batch.drop_constraint("ck_entity_kind", type_="check")
        batch.create_check_constraint(
            "ck_entity_kind",
            "kind IN ('institution','account','category',"
            "'operation','period','tag','saved_filter')",
        )
    op.execute("""CREATE TRIGGER accepted_revision_valid BEFORE UPDATE OF accepted_revision_id
        ON entities WHEN NEW.accepted_revision_id IS NOT NULL AND NOT EXISTS (
        SELECT 1 FROM entity_revisions r WHERE r.id=NEW.accepted_revision_id
        AND r.entity_id=NEW.id AND r.sealed=1)
        BEGIN SELECT RAISE(ABORT, 'invalid accepted revision'); END""")
    for _, sql in _PRESERVED_TRIGGERS:
        op.execute(sql)
    for table in ("tag_revisions", "saved_filter_revisions"):
        columns = [
            sa.Column(
                "revision_id", sa.Text, sa.ForeignKey("entity_revisions.id"), primary_key=True
            ),
            sa.Column("name", sa.Text, nullable=False),
            sa.Column("name_key", sa.Text, nullable=False),
            sa.CheckConstraint(
                "length(trim(name)) BETWEEN 1 AND 120", name="ck_" + table + "_name"
            ),
        ]
        if table == "saved_filter_revisions":
            columns.extend(
                [
                    sa.Column("query_json", sa.Text, nullable=False),
                    sa.CheckConstraint("length(query_json)<=65536", name="ck_filter_json_size"),
                ]
            )
        op.create_table(table, *columns, sqlite_strict=True)
        op.create_index("ix_" + table + "_name", table, ["name_key", "revision_id"])
    op.create_table(
        "operation_tags",
        sa.Column("revision_id", sa.Text, sa.ForeignKey("entity_revisions.id"), primary_key=True),
        sa.Column("tag_id", sa.Text, sa.ForeignKey("entities.id"), primary_key=True),
        sqlite_strict=True,
    )
    op.create_index("ix_operation_tag", "operation_tags", ["tag_id", "revision_id"])
    for table in ("tag_revisions", "saved_filter_revisions", "operation_tags"):
        for action in ("UPDATE", "DELETE"):
            op.execute(f"""CREATE TRIGGER {table}_no_{action.lower()} BEFORE {action} ON {table}
                WHEN EXISTS (SELECT 1 FROM entity_revisions r
                WHERE r.id=OLD.revision_id AND r.sealed=1)
                BEGIN SELECT RAISE(ABORT, 'revision is sealed'); END""")
        op.execute(f"""CREATE TRIGGER {table}_no_insert_sealed BEFORE INSERT ON {table}
            WHEN EXISTS (SELECT 1 FROM entity_revisions r WHERE r.id=NEW.revision_id AND r.sealed=1)
            BEGIN SELECT RAISE(ABORT, 'revision is sealed'); END""")
    tables = {
        "institution": "institution_revisions",
        "account": "account_revisions",
        "category": "category_revisions",
        "operation": "operation_revisions",
        "period": "period_revisions",
        "tag": "tag_revisions",
        "saved_filter": "saved_filter_revisions",
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
    op.execute("""CREATE TRIGGER organization_parent_valid BEFORE UPDATE OF sealed
        ON entity_revisions
        WHEN NEW.sealed=1 AND OLD.sealed=0 AND EXISTS (
        SELECT 1 FROM entities WHERE id=NEW.entity_id AND kind IN ('tag','saved_filter')) AND (
            (SELECT count(*) FROM revision_parents WHERE revision_id=NEW.id)>1
            OR EXISTS (SELECT 1 FROM entities e WHERE e.id=NEW.entity_id
                AND e.accepted_revision_id IS NULL
                AND (NEW.is_deleted=1 OR EXISTS (
                    SELECT 1 FROM revision_parents WHERE revision_id=NEW.id)))
            OR EXISTS (SELECT 1 FROM entities e WHERE e.id=NEW.entity_id
                AND e.accepted_revision_id IS NOT NULL
                AND NOT EXISTS (SELECT 1 FROM revision_parents p WHERE p.revision_id=NEW.id
                    AND p.parent_id=e.accepted_revision_id)))
        BEGIN SELECT RAISE(ABORT, 'invalid organization parent'); END""")
    for kind, table in (("tag", "tag_revisions"), ("saved_filter", "saved_filter_revisions")):
        op.execute(f"""CREATE TRIGGER {kind}_name_unique BEFORE UPDATE OF sealed ON entity_revisions
            WHEN NEW.sealed=1 AND OLD.sealed=0 AND NEW.is_deleted=0 AND EXISTS (
            SELECT 1 FROM entities own JOIN {table} value ON value.revision_id=NEW.id
            JOIN entities other ON other.book_id=own.book_id
            AND other.kind='{kind}' AND other.id<>own.id
            JOIN entity_revisions r ON r.id=other.accepted_revision_id AND r.is_deleted=0
            JOIN {table} previous ON previous.revision_id=r.id
            WHERE own.id=NEW.entity_id AND own.kind='{kind}' AND previous.name_key=value.name_key)
            BEGIN SELECT RAISE(ABORT, 'active name is not unique'); END""")
    op.execute("""CREATE TRIGGER financial_tags_valid BEFORE UPDATE OF sealed ON entity_revisions
        WHEN NEW.sealed=1 AND OLD.sealed=0 AND EXISTS (
        SELECT 1 FROM entities WHERE id=NEW.entity_id AND kind='operation') AND (
        (SELECT count(*) FROM operation_tags WHERE revision_id=NEW.id)>100 OR EXISTS (
            SELECT 1 FROM operation_tags member JOIN entities own ON own.id=NEW.entity_id
            WHERE member.revision_id=NEW.id AND NOT EXISTS (
                SELECT 1 FROM entities tag JOIN entity_revisions r ON r.id=tag.accepted_revision_id
                JOIN tag_revisions p ON p.revision_id=r.id WHERE tag.id=member.tag_id
                AND tag.book_id=own.book_id AND tag.kind='tag' AND r.sealed=1
                AND (r.is_deleted=0 OR EXISTS (SELECT 1 FROM operation_tags previous
                    WHERE previous.revision_id=own.accepted_revision_id AND previous.tag_id=tag.id))
            )
        ))
        BEGIN SELECT RAISE(ABORT, 'invalid operation tags'); END""")


def downgrade() -> None:
    raise RuntimeError("Понижение финансовой схемы запрещено; используйте исходную книгу.")
