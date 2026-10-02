"""answer sessions for Stats

Revision ID: a4c6e8f0b2d5
Revises: f3b5d7e9a1c4
Create Date: 2026-09-28 19:00:00.000000

answer_events.session_ref groups answers into the sitting they came from ('quiz:<attempt id>',
'dose:<daily session id>', 'mock:<paper id>', 'duel:<id>', 'practice:<date>'), so Stats can show every session
and every feature, not only finished Mock Builder quizzes. quiz_attempts.label keeps the name the session was
started under ("Past papers · FCPS Part 1 · 2024 · Physiology").
"""

from typing import Sequence, Union

from alembic import op


# revision identifiers, used by Alembic.
revision: str = 'a4c6e8f0b2d5'
down_revision: Union[str, None] = 'f3b5d7e9a1c4'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("ALTER TABLE answer_events ADD COLUMN IF NOT EXISTS session_ref TEXT")
    op.execute("ALTER TABLE quiz_attempts ADD COLUMN IF NOT EXISTS label TEXT")
    op.execute("CREATE INDEX IF NOT EXISTS ix_answer_events_user_created ON answer_events (user_id, created_at)")


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_answer_events_user_created")
    op.execute("ALTER TABLE quiz_attempts DROP COLUMN IF EXISTS label")
    op.execute("ALTER TABLE answer_events DROP COLUMN IF EXISTS session_ref")
