"""figures and coverage on a revision sheet

Revision ID: b6e2f8a0c4d7
Revises: a4c6e8f0b2d5
Create Date: 2026-09-28 20:30:00.000000

Two columns for the book-scope revision sheet (topic_summaries):

  figures   the captioned figures found on the pages the sheet was written from, so
            revising a chapter shows its diagrams too.
  coverage  how much of the scope was actually read. A chapter can hold hundreds of
            paragraphs and a sheet reads a budgeted slice of them, so without this a
            partial read is indistinguishable from a complete one.

Both are left empty for Rapid Review sheets, which are built from questions rather
than from pages.
"""

from typing import Sequence, Union

from alembic import op


# revision identifiers, used by Alembic.
revision: str = 'b6e2f8a0c4d7'
down_revision: Union[str, None] = 'a4c6e8f0b2d5'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("ALTER TABLE topic_summaries ADD COLUMN IF NOT EXISTS figures JSONB NOT NULL DEFAULT '[]'::jsonb")
    op.execute("ALTER TABLE topic_summaries ADD COLUMN IF NOT EXISTS coverage JSONB NOT NULL DEFAULT '{}'::jsonb")


def downgrade() -> None:
    op.execute("ALTER TABLE topic_summaries DROP COLUMN IF EXISTS coverage")
    op.execute("ALTER TABLE topic_summaries DROP COLUMN IF EXISTS figures")
