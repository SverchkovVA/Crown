"""Локальный аудит подтверждённого импорта; финансовые форматы не меняются."""

import sqlalchemy as sa
from alembic import op

revision = "0008"
down_revision = "0007"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "import_batches",
        sa.Column("id", sa.Text, primary_key=True),
        sa.Column("book_id", sa.Text, sa.ForeignKey("books.id"), nullable=False),
        sa.Column("account_id", sa.Text, sa.ForeignKey("entities.id"), nullable=False),
        sa.Column("kind", sa.Text, nullable=False),
        sa.Column("file_hash", sa.Text, nullable=False),
        sa.Column("scope_hash", sa.Text, nullable=False),
        sa.Column("command_id", sa.Text, nullable=False, unique=True),
        sa.Column("command_hash", sa.Text, nullable=False),
        sa.Column("group_id", sa.Text, sa.ForeignKey("change_groups.id"), nullable=False),
        sa.Column("created_at", sa.Text, nullable=False),
        sa.Column("item_count", sa.Integer, nullable=False),
        sa.Column("sealed", sa.Integer, nullable=False, server_default="0"),
        sa.CheckConstraint("kind IN ('csv','ofx') AND sealed IN (0,1)", name="ck_import_kind"),
        sa.CheckConstraint("item_count BETWEEN 1 AND 2000", name="ck_import_count"),
        sa.CheckConstraint(
            "length(file_hash)=64 AND length(scope_hash)=64 AND length(command_hash)=64",
            name="ck_import_hashes",
        ),
        sqlite_strict=True,
    )
    op.create_table(
        "import_items",
        sa.Column("batch_id", sa.Text, sa.ForeignKey("import_batches.id"), primary_key=True),
        sa.Column("position", sa.Integer, primary_key=True),
        sa.Column("book_id", sa.Text, sa.ForeignKey("books.id"), nullable=False),
        sa.Column("account_id", sa.Text, sa.ForeignKey("entities.id"), nullable=False),
        sa.Column("source_key", sa.Text, nullable=False),
        sa.Column("source_hash", sa.Text, nullable=False),
        sa.Column("source_json", sa.Text, nullable=False),
        sa.Column("operation_id", sa.Text, sa.ForeignKey("entities.id"), nullable=False),
        sa.Column("revision_id", sa.Text, sa.ForeignKey("entity_revisions.id"), nullable=False),
        sa.Column("linked", sa.Integer, nullable=False),
        sa.Column("acknowledged_duplicate", sa.Integer, nullable=False),
        sa.UniqueConstraint("book_id", "account_id", "source_key", name="uq_import_source"),
        sa.CheckConstraint(
            "position BETWEEN 1 AND 2000 AND linked IN (0,1) AND acknowledged_duplicate IN (0,1)",
            name="ck_import_item_flags",
        ),
        sa.CheckConstraint(
            "length(source_key)=64 AND length(source_hash)=64 AND length(source_json)<=4096",
            name="ck_import_source",
        ),
        sqlite_strict=True,
    )
    op.execute("""CREATE TRIGGER import_batch_account_valid BEFORE INSERT ON import_batches
        WHEN NOT EXISTS (SELECT 1 FROM entities e JOIN entity_revisions r
        ON r.id=e.accepted_revision_id JOIN account_revisions a ON a.revision_id=r.id
        WHERE e.id=NEW.account_id AND e.book_id=NEW.book_id AND e.kind='account'
        AND r.sealed=1 AND r.is_deleted=0 AND a.currency='RUB') OR NOT EXISTS (
        SELECT 1 FROM change_groups WHERE id=NEW.group_id AND book_id=NEW.book_id)
        BEGIN SELECT RAISE(ABORT, 'invalid import account or group'); END""")
    op.execute("""CREATE TRIGGER import_batch_no_update BEFORE UPDATE ON import_batches
        WHEN OLD.sealed=1 BEGIN SELECT RAISE(ABORT, 'import is sealed'); END""")
    op.execute("""CREATE TRIGGER import_batch_no_delete BEFORE DELETE ON import_batches
        BEGIN SELECT RAISE(ABORT, 'import is immutable'); END""")
    op.execute("""CREATE TRIGGER import_batch_seal_valid BEFORE UPDATE OF sealed ON import_batches
        WHEN NEW.sealed=1 AND OLD.sealed=0 AND (
        (SELECT count(*) FROM import_items WHERE batch_id=NEW.id)<>NEW.item_count)
        BEGIN SELECT RAISE(ABORT, 'incomplete import'); END""")
    for action in ("UPDATE", "DELETE"):
        op.execute(f"""CREATE TRIGGER import_item_no_{action.lower()}
            BEFORE {action} ON import_items
            BEGIN SELECT RAISE(ABORT, 'import item is immutable'); END""")
    op.execute("""CREATE TRIGGER import_item_valid BEFORE INSERT ON import_items
        WHEN NOT EXISTS (
        SELECT 1 FROM import_batches b JOIN entities e ON e.id=NEW.operation_id
        JOIN entity_revisions r ON r.id=NEW.revision_id AND r.entity_id=e.id
        WHERE b.id=NEW.batch_id AND b.sealed=0 AND b.book_id=NEW.book_id
        AND b.account_id=NEW.account_id AND e.book_id=b.book_id AND e.kind='operation'
        AND r.sealed=1 AND r.is_deleted=0 AND (NEW.linked=1 OR r.group_id=b.group_id)
        AND EXISTS (SELECT 1 FROM postings p JOIN ledger_accounts l ON l.id=p.ledger_account_id
            WHERE p.revision_id=r.id AND l.role='asset' AND l.reference_id=b.account_id))
        BEGIN SELECT RAISE(ABORT, 'invalid import item'); END""")


def downgrade() -> None:
    raise RuntimeError("Понижение схемы импорта запрещено; используйте исходную книгу.")
