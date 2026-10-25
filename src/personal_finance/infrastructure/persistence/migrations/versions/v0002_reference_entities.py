"""Банки, счета и категории без финансовых операций."""

import sqlalchemy as sa
from alembic import op

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "institution_revisions",
        sa.Column("revision_id", sa.Text, sa.ForeignKey("entity_revisions.id"), primary_key=True),
        sa.Column("name", sa.Text, nullable=False),
        sa.CheckConstraint("length(trim(name)) BETWEEN 1 AND 120", name="ck_institution_name"),
        sqlite_strict=True,
    )
    op.create_table(
        "account_revisions",
        sa.Column("revision_id", sa.Text, sa.ForeignKey("entity_revisions.id"), primary_key=True),
        sa.Column("name", sa.Text, nullable=False),
        sa.Column("kind", sa.Text, nullable=False),
        sa.Column("institution_id", sa.Text, sa.ForeignKey("entities.id")),
        sa.Column("currency", sa.Text, sa.ForeignKey("currencies.code"), nullable=False),
        sa.CheckConstraint("length(trim(name)) BETWEEN 1 AND 120", name="ck_account_name"),
        sa.CheckConstraint(
            "kind IN ('debit','savings','cash','investment')", name="ck_account_kind"
        ),
        sa.CheckConstraint("kind <> 'cash' OR institution_id IS NULL", name="ck_cash_no_bank"),
        sqlite_strict=True,
    )
    op.create_table(
        "category_revisions",
        sa.Column("revision_id", sa.Text, sa.ForeignKey("entity_revisions.id"), primary_key=True),
        sa.Column("name", sa.Text, nullable=False),
        sa.Column("kind", sa.Text, nullable=False),
        sa.Column("parent_id", sa.Text, sa.ForeignKey("entities.id")),
        sa.CheckConstraint("length(trim(name)) BETWEEN 1 AND 120", name="ck_category_name"),
        sa.CheckConstraint("kind IN ('income','expense')", name="ck_category_kind"),
        sqlite_strict=True,
    )
    for table in ("institution_revisions", "account_revisions", "category_revisions"):
        for action in ("UPDATE", "DELETE"):
            op.execute(f"""CREATE TRIGGER {table}_no_{action.lower()}
                BEFORE {action} ON {table} WHEN EXISTS (
                SELECT 1 FROM entity_revisions r WHERE r.id=OLD.revision_id AND r.sealed=1)
                BEGIN SELECT RAISE(ABORT, 'revision is sealed'); END""")
        op.execute(f"""CREATE TRIGGER {table}_no_insert_sealed BEFORE INSERT ON {table}
            WHEN EXISTS (SELECT 1 FROM entity_revisions r
            WHERE r.id=NEW.revision_id AND r.sealed=1)
            BEGIN SELECT RAISE(ABORT, 'revision is sealed'); END""")
    op.execute("""CREATE TRIGGER revision_payload_valid BEFORE UPDATE OF sealed
        ON entity_revisions WHEN NEW.sealed=1 AND OLD.sealed=0 AND NOT (
        EXISTS (SELECT 1 FROM entities e JOIN institution_revisions p
                ON p.revision_id=NEW.id WHERE e.id=NEW.entity_id AND e.kind='institution')
        OR EXISTS (SELECT 1 FROM entities e JOIN account_revisions p
                ON p.revision_id=NEW.id WHERE e.id=NEW.entity_id AND e.kind='account')
        OR EXISTS (SELECT 1 FROM entities e JOIN category_revisions p
                ON p.revision_id=NEW.id WHERE e.id=NEW.entity_id AND e.kind='category'))
        BEGIN SELECT RAISE(ABORT, 'missing typed payload'); END""")


def downgrade() -> None:
    raise RuntimeError("Автоматическое понижение схемы запрещено; используйте проверенную копию.")
