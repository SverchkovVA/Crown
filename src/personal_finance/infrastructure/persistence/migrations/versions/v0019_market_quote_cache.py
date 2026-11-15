"""Пробное зашифрованное хранилище рыночных цен."""

from alembic import op

revision = "0019"
down_revision = "0018"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
    CREATE TABLE market_quote_cache (
        book_id TEXT NOT NULL REFERENCES books(id),
        provider TEXT NOT NULL CHECK (length(provider) BETWEEN 1 AND 80),
        instrument_code TEXT NOT NULL CHECK (length(instrument_code) BETWEEN 1 AND 80),
        board TEXT NOT NULL CHECK (length(board) <= 80),
        quoted_on TEXT NOT NULL CHECK (
            quoted_on GLOB '[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]'
        ),
        quoted_at TEXT CHECK (
            quoted_at IS NULL OR (length(quoted_at) BETWEEN 25 AND 32
                AND substr(quoted_at, -6) = '+00:00')
        ),
        unit TEXT NOT NULL CHECK (unit IN ('cash_per_security', 'percent_of_face')),
        value TEXT NOT NULL CHECK (length(value) BETWEEN 1 AND 32),
        currency TEXT NOT NULL CHECK (currency GLOB '[A-Z][A-Z][A-Z]'),
        exponent INTEGER NOT NULL CHECK (exponent BETWEEN 0 AND 8),
        unpaid_face_minor INTEGER,
        accrued_minor INTEGER,
        PRIMARY KEY (book_id, provider, instrument_code, board, quoted_on),
        CHECK (
            (unit = 'cash_per_security' AND unpaid_face_minor IS NULL AND accrued_minor IS NULL)
            OR (unit = 'percent_of_face' AND unpaid_face_minor IS NOT NULL
                AND unpaid_face_minor > 0 AND accrued_minor IS NOT NULL AND accrued_minor >= 0)
        )
    ) STRICT
    """)


def downgrade() -> None:
    raise RuntimeError("Понижение схемы с сохранёнными котировками не поддерживается.")
