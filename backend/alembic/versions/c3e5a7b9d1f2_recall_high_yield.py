"""recall high-yield ranking

Revision ID: c3e5a7b9d1f2
Revises: b2d4f6a8c0e1
Create Date: 2026-09-25 21:00:00.000000

Each recall gets a stem embedding and, for headline recalls, `times_asked`:
how many recalls in the bank are the same question reworded. The Daily Dose
uses it to weight new questions toward what past papers ask most.
"""

from typing import Sequence, Union

from alembic import op


# revision identifiers, used by Alembic.
revision: str = 'c3e5a7b9d1f2'
down_revision: Union[str, None] = 'b2d4f6a8c0e1'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("ALTER TABLE recall_items ADD COLUMN IF NOT EXISTS embedding vector(1024)")
    op.execute("ALTER TABLE recall_items ADD COLUMN IF NOT EXISTS times_asked integer")
    op.execute("CREATE INDEX IF NOT EXISTS idx_recall_items_mcq ON recall_items (mcq_id) WHERE mcq_id IS NOT NULL")


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS idx_recall_items_mcq")
    op.execute("ALTER TABLE recall_items DROP COLUMN IF EXISTS times_asked")
    op.execute("ALTER TABLE recall_items DROP COLUMN IF EXISTS embedding")
