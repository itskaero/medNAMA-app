"""mcq asked years

Revision ID: d9f1b3c5e7a9
Revises: c7e9a1b3d5f7
Create Date: 2026-09-26 19:00:00.000000

Every past-paper year a question was asked in, counting reworded repeats in other years
(stem embeddings, cosine >= 0.89; scripts/rank_past_papers.py). The archive already merged
exact duplicates, so without this each question looks "asked once".
"""

from typing import Sequence, Union

from alembic import op


# revision identifiers, used by Alembic.
revision: str = 'd9f1b3c5e7a9'
down_revision: Union[str, None] = 'c7e9a1b3d5f7'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("ALTER TABLE mcqs ADD COLUMN IF NOT EXISTS asked_years JSONB")


def downgrade() -> None:
    op.execute("ALTER TABLE mcqs DROP COLUMN IF EXISTS asked_years")
