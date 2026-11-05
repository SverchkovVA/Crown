"""Настройки собственных отправителей и квитанции пакетного OCR."""

from alembic import op

revision = "0011"
down_revision = "0010"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("CREATE INDEX ix_ocr_file ON ocr_records(book_id,file_sha256)")
    op.execute("""CREATE TRIGGER ocr_source_file_context BEFORE INSERT ON ocr_records
        WHEN EXISTS (SELECT 1 FROM ocr_records old WHERE old.book_id=NEW.book_id
            AND old.file_sha256=NEW.file_sha256 AND old.image_sha256<>NEW.image_sha256)
        BEGIN SELECT RAISE(ABORT,'ocr source normalization conflict'); END""")
    op.execute("""CREATE TABLE ocr_settings_versions (
        id TEXT PRIMARY KEY, book_id TEXT NOT NULL REFERENCES books(id),
        parent_id TEXT REFERENCES ocr_settings_versions(id),
        payload TEXT NOT NULL CHECK(json_valid(payload) AND length(CAST(payload AS BLOB))<=32768),
        payload_hash TEXT NOT NULL CHECK(length(payload_hash)=64 AND payload_hash NOT GLOB
                '*[^0-9a-f]*'),
        created_at TEXT NOT NULL, UNIQUE(book_id,parent_id)) STRICT""")
    op.execute(
        "CREATE UNIQUE INDEX uq_ocr_settings_initial ON "
        "ocr_settings_versions(book_id) WHERE parent_id IS NULL"
    )
    op.execute("""CREATE TABLE ocr_settings_heads (
        book_id TEXT PRIMARY KEY REFERENCES books(id),
        revision_id TEXT NOT NULL UNIQUE REFERENCES ocr_settings_versions(id)) STRICT""")
    op.execute("""CREATE TRIGGER ocr_settings_parent BEFORE INSERT ON ocr_settings_versions
        WHEN (NEW.parent_id IS NULL AND EXISTS (SELECT 1 FROM ocr_settings_heads WHERE
                book_id=NEW.book_id))
        OR (NEW.parent_id IS NOT NULL AND NOT EXISTS (SELECT 1 FROM ocr_settings_heads
            WHERE book_id=NEW.book_id AND revision_id=NEW.parent_id))
        BEGIN SELECT RAISE(ABORT,'stale ocr preferences'); END""")
    op.execute("""CREATE TRIGGER ocr_settings_head_insert BEFORE INSERT ON ocr_settings_heads
        WHEN NOT EXISTS (SELECT 1 FROM ocr_settings_versions v WHERE v.id=NEW.revision_id
            AND v.book_id=NEW.book_id AND v.parent_id IS NULL)
        BEGIN SELECT RAISE(ABORT,'invalid ocr settings head'); END""")
    op.execute("""CREATE TRIGGER ocr_settings_head_update BEFORE UPDATE ON ocr_settings_heads
        WHEN NEW.book_id<>OLD.book_id OR NOT EXISTS (SELECT 1 FROM ocr_settings_versions v
            WHERE v.id=NEW.revision_id AND v.book_id=NEW.book_id AND v.parent_id=OLD.revision_id)
        BEGIN SELECT RAISE(ABORT,'invalid ocr settings transition'); END""")
    op.execute("""CREATE TRIGGER ocr_settings_head_delete BEFORE DELETE ON ocr_settings_heads
        BEGIN SELECT RAISE(ABORT,'ocr settings head is permanent'); END""")
    op.execute("""CREATE TABLE ocr_batches (
        id TEXT PRIMARY KEY, book_id TEXT NOT NULL REFERENCES books(id),
        request_hash TEXT NOT NULL CHECK(length(request_hash)=64 AND request_hash NOT GLOB
                '*[^0-9a-f]*'),
        created_at TEXT NOT NULL) STRICT""")
    op.execute("""CREATE TABLE ocr_batch_items (
        batch_id TEXT NOT NULL REFERENCES ocr_batches(id), position INTEGER NOT NULL
                CHECK(position BETWEEN 0 AND 99),
        audit_id TEXT NOT NULL UNIQUE REFERENCES ocr_records(id),
                PRIMARY KEY(batch_id,position)) STRICT""")
    for table in ("ocr_settings_versions", "ocr_batches", "ocr_batch_items"):
        for action in ("UPDATE", "DELETE"):
            op.execute(
                f"CREATE TRIGGER {table}_no_{action.lower()} BEFORE {action} ON {table} "
                "BEGIN SELECT RAISE(ABORT,'ocr workflow history is immutable'); END"
            )
    op.execute("""CREATE TRIGGER ocr_batch_book BEFORE INSERT ON ocr_batch_items
        WHEN NOT EXISTS (SELECT 1 FROM ocr_batches b JOIN ocr_records r ON r.id=NEW.audit_id
            WHERE b.id=NEW.batch_id AND b.book_id=r.book_id)
        BEGIN SELECT RAISE(ABORT,'invalid ocr batch references'); END""")


def downgrade() -> None:
    raise RuntimeError("Понижение схемы OCR запрещено; используйте исходную книгу.")
