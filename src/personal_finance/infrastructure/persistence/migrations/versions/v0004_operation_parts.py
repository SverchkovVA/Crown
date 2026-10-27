"""Разделение доходов/расходов с сохранением старых финансовых версий."""

import sqlalchemy as sa
from alembic import op

revision = "0004"
down_revision = "0003"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "operation_parts",
        sa.Column(
            "revision_id",
            sa.Text,
            sa.ForeignKey("operation_revisions.revision_id"),
            primary_key=True,
        ),
        sa.Column("position", sa.Integer, primary_key=True),
        sa.Column("category_id", sa.Text, sa.ForeignKey("entities.id"), nullable=False),
        sa.Column("amount_minor", sa.Integer, nullable=False),
        sa.UniqueConstraint("revision_id", "category_id", name="uq_operation_part_category"),
        sa.CheckConstraint("position BETWEEN 0 AND 99", name="ck_operation_part_position"),
        sa.CheckConstraint(
            "amount_minor BETWEEN 1 AND 9223372036854775807", name="ck_operation_part_amount"
        ),
        sqlite_strict=True,
    )
    for action in ("UPDATE", "DELETE"):
        condition = "r.id=OLD.revision_id"
        if action == "UPDATE":
            condition = "r.id IN (OLD.revision_id,NEW.revision_id)"
        op.execute(f"""CREATE TRIGGER operation_parts_no_{action.lower()}
            BEFORE {action} ON operation_parts WHEN EXISTS (
            SELECT 1 FROM entity_revisions r WHERE {condition} AND r.sealed=1)
            BEGIN SELECT RAISE(ABORT, 'revision is sealed'); END""")
    op.execute("""CREATE TRIGGER operation_parts_no_insert_sealed BEFORE INSERT ON operation_parts
        WHEN EXISTS (SELECT 1 FROM entity_revisions r
        WHERE r.id=NEW.revision_id AND r.sealed=1)
        BEGIN SELECT RAISE(ABORT, 'revision is sealed'); END""")
    op.execute("DROP TRIGGER financial_revision_balanced")
    op.execute("""CREATE TRIGGER financial_revision_balanced BEFORE UPDATE OF sealed
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
        BEGIN SELECT RAISE(ABORT, 'invalid financial postings'); END""")
    op.execute("""CREATE TRIGGER financial_parts_valid BEFORE UPDATE OF sealed
        ON entity_revisions WHEN NEW.sealed=1 AND OLD.sealed=0
        AND EXISTS (SELECT 1 FROM entities WHERE id=NEW.entity_id AND kind='operation')
        AND EXISTS (
            SELECT 1 FROM operation_revisions o WHERE o.revision_id=NEW.id AND (
                (NOT EXISTS (SELECT 1 FROM operation_parts WHERE revision_id=NEW.id)
                 AND ((o.kind IN ('income','expense') AND o.category_id IS NULL)
                      OR (SELECT count(*) FROM postings WHERE revision_id=NEW.id) NOT IN (2,3)))
                OR (EXISTS (SELECT 1 FROM operation_parts WHERE revision_id=NEW.id) AND (
                    o.kind NOT IN ('income','expense') OR o.category_id IS NOT NULL
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
                            AND e.kind='category' AND c.kind=o.kind
                        )
                    )
                ))
            )
        )
        BEGIN SELECT RAISE(ABORT, 'invalid financial parts'); END""")


def downgrade() -> None:
    raise RuntimeError("Понижение финансовой схемы запрещено; используйте исходную книгу.")
