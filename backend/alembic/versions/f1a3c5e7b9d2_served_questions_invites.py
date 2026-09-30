"""questions served per session; invite-only sign-ups

Revision ID: f1a3c5e7b9d2
Revises: e7c9a1b3d5f2
Create Date: 2026-09-30 21:00:00.000000

    quiz_attempts.mcq_ids   the questions a session served. Answers used to be saved only when a session was
                            finished, so questions from an abandoned session never counted as seen and came back
                            (12 of 18 NAS sessions were never finished). Answers are now saved one by one; this
                            also lets questions shown but not answered rank after new ones.
    users.is_active, users.invited_with, invite_codes
                            sign-up by invite code (REGISTRATION_MODE), accounts the admin can disable.
"""

from typing import Sequence, Union

from alembic import op


# revision identifiers, used by Alembic.
revision: str = 'f1a3c5e7b9d2'
down_revision: Union[str, None] = 'e7c9a1b3d5f2'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("ALTER TABLE quiz_attempts ADD COLUMN IF NOT EXISTS mcq_ids JSONB")
    op.execute("CREATE INDEX IF NOT EXISTS idx_attempt_answers_attempt_mcq ON attempt_answers (quiz_attempt_id, mcq_id)")
    op.execute("""
        ALTER TABLE users
            ADD COLUMN IF NOT EXISTS is_active BOOLEAN NOT NULL DEFAULT true,
            ADD COLUMN IF NOT EXISTS invited_with TEXT
    """)
    op.execute("""
        CREATE TABLE IF NOT EXISTS invite_codes (
            id SERIAL PRIMARY KEY,
            code TEXT NOT NULL UNIQUE,
            label TEXT,
            max_uses INTEGER NOT NULL DEFAULT 1,
            uses INTEGER NOT NULL DEFAULT 0,
            expires_at TIMESTAMPTZ,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            created_by INTEGER REFERENCES users(id) ON DELETE SET NULL
        )
    """)


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS invite_codes")
    op.execute("ALTER TABLE users DROP COLUMN IF EXISTS is_active, DROP COLUMN IF EXISTS invited_with")
    op.execute("DROP INDEX IF EXISTS idx_attempt_answers_attempt_mcq")
    op.execute("ALTER TABLE quiz_attempts DROP COLUMN IF EXISTS mcq_ids")
