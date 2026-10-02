"""add_answer_reports

Revision ID: c9e3a5b7d2f4
Revises: b7d2e4f6a8c1
Create Date: 2026-09-24 18:00:00.000000

Users can flag a chat answer or an MCQ (wrong answer, wrong citation, outdated
guideline). Admins review the queue from the dashboard.
"""

from typing import Sequence, Union

from alembic import op


# revision identifiers, used by Alembic.
revision: str = 'c9e3a5b7d2f4'
down_revision: Union[str, None] = 'b7d2e4f6a8c1'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS answer_reports (
            id SERIAL PRIMARY KEY,
            user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            kind TEXT NOT NULL CHECK (kind IN ('chat', 'mcq')),
            mcq_id INTEGER REFERENCES mcqs(id) ON DELETE CASCADE,
            question TEXT,
            answer_excerpt TEXT,
            reason TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'open' CHECK (status IN ('open', 'resolved', 'dismissed')),
            created_at TIMESTAMP NOT NULL DEFAULT now(),
            resolved_at TIMESTAMP
        );
        """
    )
    op.execute("CREATE INDEX IF NOT EXISTS idx_answer_reports_status ON answer_reports (status, created_at DESC);")


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS answer_reports;")
