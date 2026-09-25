"""mcq 'private' status

Revision ID: d5f7a9c1e3b4
Revises: c3e5a7b9d1f2
Create Date: 2026-09-25 22:00:00.000000

High-yield questions written from a private recall source are stored with
status 'private': only the Daily Dose (for users allowed by HIGH_YIELD_DOSE)
serves them; quizzes, duels, re-tests and the MCQ browser skip them.
"""

from typing import Sequence, Union

from alembic import op


# revision identifiers, used by Alembic.
revision: str = 'd5f7a9c1e3b4'
down_revision: Union[str, None] = 'c3e5a7b9d1f2'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("ALTER TABLE mcqs DROP CONSTRAINT IF EXISTS mcqs_status_check")
    op.execute("ALTER TABLE mcqs ADD CONSTRAINT mcqs_status_check "
               "CHECK (status IN ('pending', 'generating', 'ready', 'failed', 'private'))")


def downgrade() -> None:
    op.execute("DELETE FROM mcqs WHERE status = 'private'")
    op.execute("ALTER TABLE mcqs DROP CONSTRAINT IF EXISTS mcqs_status_check")
    op.execute("ALTER TABLE mcqs ADD CONSTRAINT mcqs_status_check "
               "CHECK (status IN ('pending', 'generating', 'ready', 'failed'))")
