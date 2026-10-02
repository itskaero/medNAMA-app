"""mcq source

Revision ID: f8b0d2e4a6c8
Revises: e7a9c1d3f5b6
Create Date: 2026-09-26 12:00:00.000000

Where an MCQ came from when it was not generated in-app, e.g. 'seed:p1/patho.js'
for questions imported by scripts/seed_mcqs.py. Lets a re-run skip what is
already there and lets an import be removed cleanly.
"""

from typing import Sequence, Union

from alembic import op


# revision identifiers, used by Alembic.
revision: str = 'f8b0d2e4a6c8'
down_revision: Union[str, None] = 'e7a9c1d3f5b6'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("ALTER TABLE mcqs ADD COLUMN IF NOT EXISTS source TEXT")
    op.execute("CREATE INDEX IF NOT EXISTS idx_mcqs_source ON mcqs (source) WHERE source IS NOT NULL")
    op.execute("CREATE INDEX IF NOT EXISTS idx_mcqs_categories ON mcqs (main_category, sub_category)")


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS idx_mcqs_categories")
    op.execute("DROP INDEX IF EXISTS idx_mcqs_source")
    op.execute("ALTER TABLE mcqs DROP COLUMN IF EXISTS source")
