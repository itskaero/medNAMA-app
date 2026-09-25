"""weekly mock parts

Revision ID: a3c5e7f9b1d2
Revises: f8b0d2e4a6c8
Create Date: 2026-09-26 14:00:00.000000

The weekly mock is two papers, like the exam's two levels: FCPS Part 1 (all
basic-science subjects mixed) and FCPS Part 2 (one specialty's topics mixed,
or all specialties). Each is 100 MCQs in 120 minutes. A paper is now keyed by
(week_start, part, track); track is the Part 2 specialty ('' for Part 1 and
for the mixed Part 2 paper).
"""

from typing import Sequence, Union

from alembic import op


# revision identifiers, used by Alembic.
revision: str = 'a3c5e7f9b1d2'
down_revision: Union[str, None] = 'f8b0d2e4a6c8'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("ALTER TABLE weekly_mocks ADD COLUMN IF NOT EXISTS part TEXT NOT NULL DEFAULT 'p1'")
    op.execute("ALTER TABLE weekly_mocks ADD COLUMN IF NOT EXISTS track TEXT NOT NULL DEFAULT ''")
    op.execute("ALTER TABLE weekly_mocks DROP CONSTRAINT IF EXISTS weekly_mocks_week_start_key")
    op.execute("CREATE UNIQUE INDEX IF NOT EXISTS uq_weekly_mocks_week_part_track ON weekly_mocks (week_start, part, track)")


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS uq_weekly_mocks_week_part_track")
    op.execute("DELETE FROM weekly_mocks WHERE part <> 'p1' OR track <> ''")
    op.execute("ALTER TABLE weekly_mocks ADD CONSTRAINT weekly_mocks_week_start_key UNIQUE (week_start)")
    op.execute("ALTER TABLE weekly_mocks DROP COLUMN IF EXISTS track")
    op.execute("ALTER TABLE weekly_mocks DROP COLUMN IF EXISTS part")
