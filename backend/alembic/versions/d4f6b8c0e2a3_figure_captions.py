"""figure_captions

Revision ID: d4f6b8c0e2a3
Revises: c9e3a5b7d2f4
Create Date: 2026-09-24 21:00:00.000000

Figures were stored with synthetic labels ("Figure 295-47"), no captions, and
thousands of decorative images (icons, QR codes). These columns let
scripts/backfill_figure_captions.py attach the printed caption, flag decorative
images, and let retrieval pick figures by caption relevance instead of by page.
"""

from typing import Sequence, Union

from alembic import op


# revision identifiers, used by Alembic.
revision: str = 'd4f6b8c0e2a3'
down_revision: Union[str, None] = 'c9e3a5b7d2f4'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("ALTER TABLE figures ADD COLUMN IF NOT EXISTS width INTEGER;")
    op.execute("ALTER TABLE figures ADD COLUMN IF NOT EXISTS height INTEGER;")
    op.execute("ALTER TABLE figures ADD COLUMN IF NOT EXISTS is_decorative BOOLEAN;")
    op.execute("ALTER TABLE figures ADD COLUMN IF NOT EXISTS caption_source TEXT;")
    op.execute("ALTER TABLE figures ADD COLUMN IF NOT EXISTS caption_embedding vector(1024);")


def downgrade() -> None:
    op.execute("ALTER TABLE figures DROP COLUMN IF EXISTS caption_embedding;")
    op.execute("ALTER TABLE figures DROP COLUMN IF EXISTS caption_source;")
    op.execute("ALTER TABLE figures DROP COLUMN IF EXISTS is_decorative;")
    op.execute("ALTER TABLE figures DROP COLUMN IF EXISTS height;")
    op.execute("ALTER TABLE figures DROP COLUMN IF EXISTS width;")
