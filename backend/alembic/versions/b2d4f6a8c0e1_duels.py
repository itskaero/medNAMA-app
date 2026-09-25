"""duels

Revision ID: b2d4f6a8c0e1
Revises: a9c1e3f5b7d9
Create Date: 2026-09-25 19:00:00.000000

"Challenge a friend": one student creates a 10-question duel and shares the
link; each player answers the same questions once, then both see a
side-by-side result with explanations.
"""

from typing import Sequence, Union

from alembic import op


# revision identifiers, used by Alembic.
revision: str = 'b2d4f6a8c0e1'
down_revision: Union[str, None] = 'a9c1e3f5b7d9'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS duels (
            id SERIAL PRIMARY KEY,
            code TEXT NOT NULL UNIQUE,
            creator_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            title TEXT,
            mcq_ids JSONB NOT NULL,
            created_at TIMESTAMP NOT NULL DEFAULT now(),
            expires_at TIMESTAMP NOT NULL
        );
        """
    )
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS duel_entries (
            id SERIAL PRIMARY KEY,
            duel_id INTEGER NOT NULL REFERENCES duels(id) ON DELETE CASCADE,
            user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            answers JSONB NOT NULL,
            score INTEGER NOT NULL,
            time_ms INTEGER,
            finished_at TIMESTAMP NOT NULL DEFAULT now(),
            UNIQUE (duel_id, user_id)
        );
        """
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS duel_entries;")
    op.execute("DROP TABLE IF EXISTS duels;")
