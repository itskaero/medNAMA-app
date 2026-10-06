"""the student's exam programme, remembered per account

Revision ID: c4d6e8f0a2b3
Revises: b3c5d7e9f1a2
Create Date: 2026-10-06 02:00:00.000000

    users.exam_level    which exam the student is sitting (app/exam_levels.py): MBBS, FCPS-I,
                        FCPS-II, PGET, MD, MS or USMLE. NULL = general exam prep, which is the
                        behaviour every account had before this column existed.

    The level used to live only in one browser's localStorage and only reached Chat, so it was
    lost on another device and meant nothing to Revision or MCQ generation. Stored per account
    it is set once and read by all of them; each feature can still override it for one request.

    Deliberately TEXT and unconstrained rather than an enum: app/exam_levels.normalise() maps
    unknown values to NULL, so a renamed programme degrades to "general prep" instead of
    breaking existing rows, and adding a programme needs no migration.
"""

from typing import Sequence, Union

from alembic import op


# revision identifiers, used by Alembic.
revision: str = 'c4d6e8f0a2b3'
down_revision: Union[str, None] = 'b3c5d7e9f1a2'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("ALTER TABLE users ADD COLUMN IF NOT EXISTS exam_level TEXT")


def downgrade() -> None:
    op.execute("ALTER TABLE users DROP COLUMN IF EXISTS exam_level")
