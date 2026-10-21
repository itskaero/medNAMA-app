"""saved_sheets.level: the programme a saved revision sheet was written for

Revision ID: d5e7f9a1b3c4
Revises: c4d6e8f0a2b3
Create Date: 2026-10-06

Reopening a saved sheet has to ask for the same programme it was written for; the level is
part of the cache key, so without it the sheet was written again under whatever level the
student has selected now.
"""
from typing import Sequence, Union

from alembic import op

revision: str = 'd5e7f9a1b3c4'
down_revision: Union[str, None] = 'c4d6e8f0a2b3'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("ALTER TABLE saved_sheets ADD COLUMN IF NOT EXISTS level TEXT")


def downgrade() -> None:
    op.execute("ALTER TABLE saved_sheets DROP COLUMN IF EXISTS level")
