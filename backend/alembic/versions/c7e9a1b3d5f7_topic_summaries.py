"""topic summaries

Revision ID: c7e9a1b3d5f7
Revises: b5d7f9a1c3e5
Create Date: 2026-09-26 18:00:00.000000

Rapid Review: a cached one-page, textbook-cited high-yield summary per topic scope (an exam's
subject/topic selection, or a bank category). access mirrors the questions it was built from:
'restricted' when built from imported past-paper keys.
"""

from typing import Sequence, Union

from alembic import op


# revision identifiers, used by Alembic.
revision: str = 'c7e9a1b3d5f7'
down_revision: Union[str, None] = 'b5d7f9a1c3e5'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS topic_summaries (
            id SERIAL PRIMARY KEY,
            scope_key TEXT NOT NULL UNIQUE,
            label TEXT NOT NULL,
            markdown TEXT NOT NULL,
            citations JSONB NOT NULL DEFAULT '[]'::jsonb,
            key_count INTEGER NOT NULL DEFAULT 0,
            access TEXT NOT NULL DEFAULT 'open',
            created_at TIMESTAMP NOT NULL DEFAULT now()
        );
        """
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS topic_summaries")
