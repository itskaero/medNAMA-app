"""per-user saved revision sheets

Revision ID: d1e3f5a7b9c2
Revises: b6e2f8a0c4d7
Create Date: 2026-09-28 21:15:00.000000

A revision sheet a user writes from their books should land in their Study Corner,
so opening the saved list can jump straight back to the same scope. The sheet
content is cached in topic_summaries (shared, keyed by scope); this table only keeps
the per-user reminder with the scope needed to reopen it.

    saved_sheets      book_ids, chapter, topic, length of the sheet the user wrote,
                      plus the label and when it was first saved. One row per
                      (user, scope); deleting it never deletes the cached sheet.
"""

from typing import Sequence, Union

from alembic import op


# revision identifiers, used by Alembic.
revision: str = 'd1e3f5a7b9c2'
down_revision: Union[str, None] = 'b6e2f8a0c4d7'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("""
        CREATE TABLE IF NOT EXISTS saved_sheets (
            id SERIAL PRIMARY KEY,
            user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            scope_key TEXT NOT NULL,
            label TEXT NOT NULL,
            book_ids JSONB NOT NULL DEFAULT '[]'::jsonb,
            chapter TEXT,
            topic TEXT,
            length TEXT NOT NULL DEFAULT 'quick',
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            CONSTRAINT uq_saved_sheets_user_scope UNIQUE (user_id, scope_key)
        )
    """)


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS saved_sheets")