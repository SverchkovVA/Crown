"""Связанные возвраты с неизменяемой ссылкой на версию расхода."""

import sqlalchemy as sa
from alembic import op

revision = "0005"
down_revision = "0004"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("DROP TRIGGER revision_payload_valid")
    op.execute("DROP TRIGGER financial_parts_valid")
    with op.batch_alter_table(
        "operation_revisions",
        recreate="always",
        table_kwargs={"sqlite_strict": True},
    ) as batch:
        batch.drop_constraint("ck_operation_kind", type_="check")
        batch.create_check_constraint(
            "ck_operation_kind",
            "kind IN ('opening','income','expense','transfer','refund')",
        )
        batch.add_column(
            sa.Column("refund_of", sa.Text, sa.ForeignKey("entities.id", name="fk_refund_entity"))
        )
        batch.add_column(
            sa.Column(
                "refund_of_revision_id",
                sa.Text,
                sa.ForeignKey("entity_revisions.id", name="fk_refund_revision"),
            )
        )
        batch.create_check_constraint(
            "ck_refund_link",
            "(kind='refund' AND refund_of IS NOT NULL AND refund_of_revision_id IS NOT NULL) OR "
            "(kind<>'refund' AND refund_of IS NULL AND refund_of_revision_id IS NULL)",
        )
    for action in ("UPDATE", "DELETE"):
        op.execute(f"""CREATE TRIGGER operation_revisions_no_{action.lower()}
            BEFORE {action} ON operation_revisions WHEN EXISTS (
            SELECT 1 FROM entity_revisions r WHERE r.id=OLD.revision_id AND r.sealed=1)
            BEGIN SELECT RAISE(ABORT, 'revision is sealed'); END""")
    op.execute("""CREATE TRIGGER operation_revisions_no_insert_sealed
        BEFORE INSERT ON operation_revisions WHEN EXISTS (
        SELECT 1 FROM entity_revisions r WHERE r.id=NEW.revision_id AND r.sealed=1)
        BEGIN SELECT RAISE(ABORT, 'revision is sealed'); END""")
    op.create_index("ix_operation_refund", "operation_revisions", ["refund_of", "revision_id"])
    op.execute("""CREATE TRIGGER revision_payload_valid BEFORE UPDATE OF sealed
        ON entity_revisions WHEN NEW.sealed=1 AND OLD.sealed=0 AND NOT (
        EXISTS (SELECT 1 FROM entities e JOIN institution_revisions p
        ON p.revision_id=NEW.id WHERE e.id=NEW.entity_id AND e.kind='institution')
        OR EXISTS (SELECT 1 FROM entities e JOIN account_revisions p
        ON p.revision_id=NEW.id WHERE e.id=NEW.entity_id AND e.kind='account')
        OR EXISTS (SELECT 1 FROM entities e JOIN category_revisions p
        ON p.revision_id=NEW.id WHERE e.id=NEW.entity_id AND e.kind='category')
        OR EXISTS (SELECT 1 FROM entities e JOIN operation_revisions p
        ON p.revision_id=NEW.id WHERE e.id=NEW.entity_id AND e.kind='operation'))
        BEGIN SELECT RAISE(ABORT, 'missing typed payload'); END""")
    op.execute("""CREATE TRIGGER financial_parts_valid BEFORE UPDATE OF sealed
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
        BEGIN SELECT RAISE(ABORT, 'invalid financial parts'); END""")
    op.execute("""CREATE TRIGGER refund_source_valid BEFORE UPDATE OF sealed
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
        BEGIN SELECT RAISE(ABORT, 'invalid refund source'); END""")


def downgrade() -> None:
    raise RuntimeError("Понижение финансовой схемы запрещено; используйте исходную книгу.")
