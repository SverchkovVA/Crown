"""Схема 0015 хранит версии условий бюджетов и целей накоплений."""

from personal_finance.infrastructure.persistence.migrations.candidates import v0015_planning

revision = "0015"
down_revision = "0014"
branch_labels = None
depends_on = None


def upgrade() -> None:
    v0015_planning.upgrade()
