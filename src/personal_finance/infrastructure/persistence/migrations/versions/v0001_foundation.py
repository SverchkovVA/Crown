"""Книга, реплики и неизменяемые версии сущностей."""

import sqlalchemy as sa
from alembic import op

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "currencies",
        sa.Column("code", sa.Text, primary_key=True),
        sa.Column("exponent", sa.Integer, nullable=False),
        sa.CheckConstraint("exponent BETWEEN 0 AND 8", name="ck_currency_exponent"),
        sqlite_strict=True,
    )
    op.execute("INSERT INTO currencies (code, exponent) VALUES ('RUB', 2)")
    op.create_table(
        "books",
        sa.Column("id", sa.Text, primary_key=True),
        sa.Column("name", sa.Text, nullable=False),
        sa.Column("currency", sa.Text, sa.ForeignKey("currencies.code"), nullable=False),
        sa.Column("created_at", sa.Text, nullable=False),
        sqlite_strict=True,
    )
    op.create_table(
        "replicas",
        sa.Column("id", sa.Text, primary_key=True),
        sa.Column("book_id", sa.Text, sa.ForeignKey("books.id"), nullable=False),
        sa.Column("sequence", sa.Integer, nullable=False, server_default="0"),
        sa.CheckConstraint("sequence >= 0", name="ck_replica_sequence"),
        sqlite_strict=True,
    )
    op.create_table(
        "local_state",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("replica_id", sa.Text, sa.ForeignKey("replicas.id"), nullable=False),
        sa.CheckConstraint("id = 1", name="ck_local_singleton"),
        sqlite_strict=True,
    )
    op.create_table(
        "entities",
        sa.Column("id", sa.Text, primary_key=True),
        sa.Column("book_id", sa.Text, sa.ForeignKey("books.id"), nullable=False),
        sa.Column("kind", sa.Text, nullable=False),
        sa.Column("accepted_revision_id", sa.Text, sa.ForeignKey("entity_revisions.id")),
        sa.CheckConstraint("kind IN ('institution','account','category')", name="ck_entity_kind"),
        sqlite_strict=True,
    )
    op.create_table(
        "change_groups",
        sa.Column("id", sa.Text, primary_key=True),
        sa.Column("book_id", sa.Text, sa.ForeignKey("books.id"), nullable=False),
        sa.Column("created_at", sa.Text, nullable=False),
        sqlite_strict=True,
    )
    op.create_table(
        "entity_revisions",
        sa.Column("id", sa.Text, primary_key=True),
        sa.Column("entity_id", sa.Text, sa.ForeignKey("entities.id"), nullable=False),
        sa.Column("replica_id", sa.Text, sa.ForeignKey("replicas.id"), nullable=False),
        sa.Column("sequence", sa.Integer, nullable=False),
        sa.Column("group_id", sa.Text, sa.ForeignKey("change_groups.id"), nullable=False),
        sa.Column("created_at", sa.Text, nullable=False),
        sa.Column("content_hash", sa.Text, nullable=False),
        sa.Column("sealed", sa.Integer, nullable=False, server_default="0"),
        sa.Column("is_deleted", sa.Integer, nullable=False, server_default="0"),
        sa.UniqueConstraint("replica_id", "sequence", name="uq_revision_sequence"),
        sa.CheckConstraint("sealed IN (0,1) AND is_deleted IN (0,1)", name="ck_revision_flags"),
        sqlite_strict=True,
    )
    op.create_table(
        "revision_parents",
        sa.Column("revision_id", sa.Text, sa.ForeignKey("entity_revisions.id"), primary_key=True),
        sa.Column("parent_id", sa.Text, sa.ForeignKey("entity_revisions.id"), primary_key=True),
        sa.CheckConstraint("revision_id <> parent_id", name="ck_revision_parent"),
        sqlite_strict=True,
    )
    op.create_table(
        "revision_heads",
        sa.Column("entity_id", sa.Text, sa.ForeignKey("entities.id"), primary_key=True),
        sa.Column("revision_id", sa.Text, sa.ForeignKey("entity_revisions.id"), primary_key=True),
        sqlite_strict=True,
    )
    op.create_table(
        "change_log",
        sa.Column("id", sa.Text, primary_key=True),
        sa.Column("replica_id", sa.Text, sa.ForeignKey("replicas.id"), nullable=False),
        sa.Column("sequence", sa.Integer, nullable=False),
        sa.Column("group_id", sa.Text, sa.ForeignKey("change_groups.id"), nullable=False),
        sa.Column("revision_id", sa.Text, sa.ForeignKey("entity_revisions.id"), nullable=False),
        sa.UniqueConstraint("replica_id", "sequence", name="uq_change_sequence"),
        sa.UniqueConstraint("revision_id", name="uq_change_revision"),
        sqlite_strict=True,
    )
    op.create_table(
        "command_receipts",
        sa.Column("id", sa.Text, primary_key=True),
        sa.Column("command_hash", sa.Text, nullable=False),
        sa.Column("entity_id", sa.Text, sa.ForeignKey("entities.id"), nullable=False),
        sa.Column("revision_id", sa.Text, sa.ForeignKey("entity_revisions.id"), nullable=False),
        sqlite_strict=True,
    )
    op.execute("""CREATE TRIGGER revision_no_update BEFORE UPDATE ON entity_revisions
        WHEN OLD.sealed=1 BEGIN SELECT RAISE(ABORT, 'revision is sealed'); END""")
    op.execute("""CREATE TRIGGER revision_no_delete BEFORE DELETE ON entity_revisions
        WHEN OLD.sealed=1 BEGIN SELECT RAISE(ABORT, 'revision is sealed'); END""")
    op.execute("""CREATE TRIGGER accepted_revision_valid BEFORE UPDATE OF accepted_revision_id
        ON entities WHEN NEW.accepted_revision_id IS NOT NULL AND NOT EXISTS (
        SELECT 1 FROM entity_revisions r WHERE r.id=NEW.accepted_revision_id
        AND r.entity_id=NEW.id AND r.sealed=1)
        BEGIN SELECT RAISE(ABORT, 'invalid accepted revision'); END""")


def downgrade() -> None:
    raise RuntimeError("Автоматическое понижение схемы запрещено; используйте проверенную копию.")
