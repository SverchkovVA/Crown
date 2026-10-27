"""Атомарные финансовые операции и внутренние счета."""

import sqlalchemy as sa
from alembic import op

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("DROP TRIGGER revision_payload_valid")
    with op.batch_alter_table(
        "entities", recreate="always", table_kwargs={"sqlite_strict": True}
    ) as batch:
        batch.drop_constraint("ck_entity_kind", type_="check")
        batch.create_check_constraint(
            "ck_entity_kind", "kind IN ('institution','account','category','operation')"
        )
    op.execute("""CREATE TRIGGER accepted_revision_valid BEFORE UPDATE OF accepted_revision_id
        ON entities WHEN NEW.accepted_revision_id IS NOT NULL AND NOT EXISTS (
        SELECT 1 FROM entity_revisions r WHERE r.id=NEW.accepted_revision_id
        AND r.entity_id=NEW.id AND r.sealed=1)
        BEGIN SELECT RAISE(ABORT, 'invalid accepted revision'); END""")
    op.create_table(
        "ledger_accounts",
        sa.Column("id", sa.Text, primary_key=True),
        sa.Column("book_id", sa.Text, sa.ForeignKey("books.id"), nullable=False),
        sa.Column("role", sa.Text, nullable=False),
        sa.Column("reference_id", sa.Text, sa.ForeignKey("entities.id")),
        sa.Column("currency", sa.Text, sa.ForeignKey("currencies.code"), nullable=False),
        sa.UniqueConstraint(
            "book_id", "role", "reference_id", "currency", name="uq_ledger_account"
        ),
        sa.CheckConstraint("role IN ('asset','income','expense','opening')", name="ck_ledger_role"),
        sa.CheckConstraint(
            "(role='opening' AND reference_id IS NULL) OR "
            "(role<>'opening' AND reference_id IS NOT NULL)",
            name="ck_ledger_reference",
        ),
        sqlite_strict=True,
    )
    op.execute("""CREATE UNIQUE INDEX uq_ledger_opening
        ON ledger_accounts(book_id,currency) WHERE role='opening'""")
    op.create_table(
        "operation_revisions",
        sa.Column("revision_id", sa.Text, sa.ForeignKey("entity_revisions.id"), primary_key=True),
        sa.Column("kind", sa.Text, nullable=False),
        sa.Column("occurred_on", sa.Text, nullable=False),
        sa.Column("account_id", sa.Text, sa.ForeignKey("entities.id"), nullable=False),
        sa.Column("amount_minor", sa.Integer, nullable=False),
        sa.Column("currency", sa.Text, sa.ForeignKey("currencies.code"), nullable=False),
        sa.Column("category_id", sa.Text, sa.ForeignKey("entities.id")),
        sa.Column("destination_account_id", sa.Text, sa.ForeignKey("entities.id")),
        sa.Column("fee_minor", sa.Integer, nullable=False),
        sa.Column("fee_category_id", sa.Text, sa.ForeignKey("entities.id")),
        sa.Column("note", sa.Text, nullable=False),
        sa.CheckConstraint(
            "kind IN ('opening','income','expense','transfer')", name="ck_operation_kind"
        ),
        sa.CheckConstraint(
            "length(occurred_on)=10 AND occurred_on=date(occurred_on)",
            name="ck_operation_date",
        ),
        sa.CheckConstraint(
            "amount_minor BETWEEN -9223372036854775807 AND 9223372036854775807 "
            "AND (kind='opening' OR amount_minor>0)",
            name="ck_operation_amount",
        ),
        sa.CheckConstraint("fee_minor>=0", name="ck_operation_fee"),
        sa.CheckConstraint("length(note)<=500", name="ck_operation_note"),
        sqlite_strict=True,
    )
    op.create_table(
        "postings",
        sa.Column(
            "revision_id",
            sa.Text,
            sa.ForeignKey("operation_revisions.revision_id"),
            primary_key=True,
        ),
        sa.Column("position", sa.Integer, primary_key=True),
        sa.Column(
            "ledger_account_id", sa.Text, sa.ForeignKey("ledger_accounts.id"), nullable=False
        ),
        sa.Column("amount_minor", sa.Integer, nullable=False),
        sa.CheckConstraint("position>=0", name="ck_posting_position"),
        sa.CheckConstraint(
            "amount_minor BETWEEN -9223372036854775807 AND 9223372036854775807",
            name="ck_posting_amount",
        ),
        sqlite_strict=True,
    )
    op.create_index("ix_postings_account", "postings", ["ledger_account_id", "revision_id"])
    op.create_index("ix_operation_date", "operation_revisions", ["occurred_on", "revision_id"])
    for table in ("operation_revisions", "postings"):
        for action in ("UPDATE", "DELETE"):
            op.execute(f"""CREATE TRIGGER {table}_no_{action.lower()}
                BEFORE {action} ON {table} WHEN EXISTS (
                SELECT 1 FROM entity_revisions r WHERE r.id=OLD.revision_id AND r.sealed=1)
                BEGIN SELECT RAISE(ABORT, 'revision is sealed'); END""")
        op.execute(f"""CREATE TRIGGER {table}_no_insert_sealed BEFORE INSERT ON {table}
            WHEN EXISTS (SELECT 1 FROM entity_revisions r
            WHERE r.id=NEW.revision_id AND r.sealed=1)
            BEGIN SELECT RAISE(ABORT, 'revision is sealed'); END""")
    for action in ("UPDATE", "DELETE"):
        op.execute(f"""CREATE TRIGGER revision_parents_no_{action.lower()}
            BEFORE {action} ON revision_parents WHEN EXISTS (
            SELECT 1 FROM entity_revisions r WHERE r.id=OLD.revision_id AND r.sealed=1)
            BEGIN SELECT RAISE(ABORT, 'revision is sealed'); END""")
    op.execute("""CREATE TRIGGER revision_parents_no_insert_sealed BEFORE INSERT ON revision_parents
        WHEN EXISTS (SELECT 1 FROM entity_revisions r WHERE r.id=NEW.revision_id AND r.sealed=1)
        BEGIN SELECT RAISE(ABORT, 'revision is sealed'); END""")
    op.execute("""CREATE TRIGGER financial_parent_valid BEFORE UPDATE OF sealed ON entity_revisions
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
        BEGIN SELECT RAISE(ABORT, 'invalid financial parent'); END""")
    for action in ("UPDATE", "DELETE"):
        op.execute(f"""CREATE TRIGGER ledger_accounts_no_{action.lower()}
            BEFORE {action} ON ledger_accounts
            BEGIN SELECT RAISE(ABORT, 'ledger account is immutable'); END""")
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
    op.execute("""CREATE TRIGGER financial_revision_balanced BEFORE UPDATE OF sealed
        ON entity_revisions WHEN NEW.sealed=1 AND OLD.sealed=0
        AND EXISTS (SELECT 1 FROM entities WHERE id=NEW.entity_id AND kind='operation')
        AND (
            (SELECT count(*) FROM postings WHERE revision_id=NEW.id) NOT IN (2,3)
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


def downgrade() -> None:
    raise RuntimeError("Понижение финансовой схемы запрещено; используйте исходную книгу.")
