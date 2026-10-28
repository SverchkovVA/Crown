"""Аудит OCR без изменения прежних финансовых таблиц и защит."""

import sqlalchemy as sa
from alembic import op

revision = "0010"
down_revision = "0009"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "ocr_records",
        sa.Column("id", sa.Text, primary_key=True),
        sa.Column("book_id", sa.Text, sa.ForeignKey("books.id"), nullable=False),
        sa.Column("account_id", sa.Text, sa.ForeignKey("entities.id"), nullable=False),
        sa.Column("bank", sa.Text, nullable=False),
        *(
            sa.Column(name, sa.Text, nullable=False)
            for name in (
                "file_sha256",
                "image_sha256",
                "document_sha256",
                "source_key",
                "evidence_hash",
                "evidence_json",
            )
        ),
        sa.Column("operation_id", sa.Text, sa.ForeignKey("entities.id"), nullable=False),
        sa.Column("revision_id", sa.Text, sa.ForeignKey("entity_revisions.id"), nullable=False),
        sa.Column("group_id", sa.Text, sa.ForeignKey("change_groups.id"), nullable=False),
        sa.Column(
            "command_id", sa.Text, sa.ForeignKey("command_receipts.id"), nullable=False, unique=True
        ),
        sa.Column("command_hash", sa.Text, nullable=False),
        sa.Column("linked", sa.Integer, nullable=False),
        sa.Column("acknowledged_duplicate", sa.Integer, nullable=False),
        sa.Column("created_at", sa.Text, nullable=False),
        sa.UniqueConstraint("book_id", "source_key", name="uq_ocr_source"),
        sa.UniqueConstraint(
            "book_id", "image_sha256", "operation_id", "account_id", name="uq_ocr_image_movement"
        ),
        sa.CheckConstraint(
            "bank IN ('Т-Банк','ВТБ') AND linked IN (0,1) AND acknowledged_duplicate IN (0,1)",
            name="ck_ocr_flags",
        ),
        sa.CheckConstraint(
            "length(evidence_json)<=131072 AND json_valid(evidence_json)", name="ck_ocr_evidence"
        ),
        sa.CheckConstraint(
            " AND ".join(
                f"length({name})=64 AND {name} NOT GLOB '*[^0-9a-f]*'"
                for name in (
                    "file_sha256",
                    "image_sha256",
                    "document_sha256",
                    "source_key",
                    "evidence_hash",
                    "command_hash",
                )
            ),
            name="ck_ocr_hashes",
        ),
        sqlite_strict=True,
    )
    op.create_index("ix_ocr_image", "ocr_records", ["book_id", "image_sha256"])
    for action in ("UPDATE", "DELETE"):
        op.execute(
            f"CREATE TRIGGER ocr_no_{action.lower()} BEFORE {action} ON ocr_records "
            "BEGIN SELECT RAISE(ABORT, 'ocr is immutable'); END"
        )
    op.execute("""CREATE TRIGGER ocr_record_valid BEFORE INSERT ON ocr_records
        WHEN NOT EXISTS (
            SELECT 1 FROM entities e JOIN entity_revisions r
            ON r.id=NEW.revision_id AND r.entity_id=e.id
            JOIN operation_revisions o ON o.revision_id=r.id
            JOIN command_receipts c ON c.id=NEW.command_id
            JOIN change_groups g ON g.id=NEW.group_id AND g.book_id=NEW.book_id
            JOIN entities a ON a.id=NEW.account_id AND a.book_id=NEW.book_id AND a.kind='account'
            JOIN entity_revisions ar ON ar.id=a.accepted_revision_id
            AND ar.sealed=1 AND ar.is_deleted=0
            JOIN account_revisions av ON av.revision_id=ar.id AND av.currency='RUB'
            WHERE e.id=NEW.operation_id AND e.book_id=NEW.book_id AND e.kind='operation'
            AND r.sealed=1 AND r.is_deleted=0 AND o.kind<>'opening'
            AND (NEW.linked=1 OR r.group_id=NEW.group_id)
            AND c.command_hash=NEW.command_hash AND c.entity_id=e.id AND c.revision_id=r.id
            AND NEW.image_sha256=json_extract(NEW.evidence_json,'$.image_sha256')
            AND NEW.document_sha256=json_extract(NEW.evidence_json,'$.document_sha256')
            AND NEW.file_sha256=json_extract(NEW.evidence_json,'$.file_sha256')
            AND NEW.bank=json_extract(NEW.evidence_json,'$.bank')
            AND json_type(NEW.evidence_json,'$.reviewed.minor')='integer'
            AND json_extract(NEW.evidence_json,'$.reviewed.minor')>0
            AND json_extract(NEW.evidence_json,'$.reviewed.currency')='RUB'
            AND json_extract(NEW.evidence_json,'$.reviewed.direction') IN ('in','out')
            AND o.occurred_on=json_extract(NEW.evidence_json,'$.reviewed.occurred_on')
            AND (SELECT coalesce(sum(p.amount_minor),0) FROM postings p JOIN ledger_accounts l
                ON l.id=p.ledger_account_id WHERE p.revision_id=r.id AND l.role='asset'
                AND l.reference_id=NEW.account_id AND l.currency='RUB') =
                json_extract(NEW.evidence_json,'$.reviewed.minor') * CASE
                json_extract(NEW.evidence_json,'$.reviewed.direction') WHEN 'in' THEN 1 ELSE -1 END
        ) BEGIN SELECT RAISE(ABORT, 'invalid ocr references or movement'); END""")
    op.execute("""CREATE TRIGGER ocr_context_valid BEFORE INSERT ON ocr_records
        WHEN EXISTS (SELECT 1 FROM ocr_records old WHERE old.book_id=NEW.book_id
        AND old.image_sha256=NEW.image_sha256 AND (old.document_sha256<>NEW.document_sha256
            OR old.bank<>NEW.bank))
        OR EXISTS (SELECT 1 FROM ocr_records old,
            json_each(old.evidence_json,'$.fields.amount.words') x,
            json_each(NEW.evidence_json,'$.fields.amount.words') y
            WHERE old.book_id=NEW.book_id AND old.image_sha256=NEW.image_sha256 AND x.value=y.value)
        BEGIN SELECT RAISE(ABORT, 'ocr context conflict'); END""")


def downgrade() -> None:
    raise RuntimeError("Понижение схемы OCR запрещено; используйте исходную книгу.")
