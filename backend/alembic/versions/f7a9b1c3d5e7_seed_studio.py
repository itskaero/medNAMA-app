"""Seed Studio tables: TOS documents, pages and nodes; seeding jobs, batches, items, notes and the audit log

Revision ID: f7a9b1c3d5e7
Revises: e6f8a0b2c4d5
Create Date: 2026-10-09
"""
from typing import Sequence, Union

from alembic import op

revision: str = 'f7a9b1c3d5e7'
down_revision: Union[str, None] = 'e6f8a0b2c4d5'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

TABLES = ("seed_tos_documents", "seed_tos_pages", "seed_tos_nodes", "seed_jobs", "seed_batches", "seed_items",
          "seed_notes", "seed_audit")


def upgrade() -> None:
    # The tables are defined once, in app/seed_models.py; created here from that definition (new tables only,
    # nothing existing is altered).
    from app.models import Base
    import app.seed_models  # noqa: F401

    Base.metadata.create_all(bind=op.get_bind(), tables=[Base.metadata.tables[t] for t in TABLES])


def downgrade() -> None:
    for t in reversed(TABLES):
        op.drop_table(t)
