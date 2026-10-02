"""study modes: mistake types, confusable pairs, final sprint, weekly mock

Revision ID: e7a9c1d3f5b6
Revises: d5f7a9c1e3b4
Create Date: 2026-09-26 10:00:00.000000

  answer_events.mistake_type  confusion | misconception | gap (wrong answers only)
  confusable_pairs            two look-alike concepts a student mixed up: a
                              textbook-grounded comparison and 2 telling-apart MCQs
  answer_events.pair_id       the pair a 'confusion' answer produced
  study_sessions              non-Daily-Dose sessions (the final sprint)
  weekly_mocks / entries      one fixed CPSP-format paper per ISO week + results
"""

from typing import Sequence, Union

from alembic import op


# revision identifiers, used by Alembic.
revision: str = 'e7a9c1d3f5b6'
down_revision: Union[str, None] = 'd5f7a9c1e3b4'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS confusable_pairs (
            id SERIAL PRIMARY KEY,
            pair_key TEXT NOT NULL UNIQUE,
            term_a TEXT NOT NULL,
            term_b TEXT NOT NULL,
            card JSONB,
            mcq_ids JSONB NOT NULL DEFAULT '[]'::jsonb,
            grounding TEXT NOT NULL DEFAULT 'textbook',
            status TEXT NOT NULL DEFAULT 'pending' CHECK (status IN ('pending', 'ready', 'failed')),
            created_at TIMESTAMP NOT NULL DEFAULT now()
        );
        """
    )
    op.execute("ALTER TABLE answer_events ADD COLUMN IF NOT EXISTS mistake_type TEXT")
    op.execute("ALTER TABLE answer_events ADD COLUMN IF NOT EXISTS pair_id INTEGER "
               "REFERENCES confusable_pairs(id) ON DELETE SET NULL")
    op.execute("CREATE INDEX IF NOT EXISTS idx_answer_events_user_mistake ON answer_events (user_id, mistake_type)")
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS study_sessions (
            id SERIAL PRIMARY KEY,
            user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            kind TEXT NOT NULL,
            day DATE NOT NULL,
            items JSONB NOT NULL DEFAULT '[]'::jsonb,
            completed_at TIMESTAMP,
            created_at TIMESTAMP NOT NULL DEFAULT now(),
            UNIQUE (user_id, kind, day)
        );
        """
    )
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS weekly_mocks (
            id SERIAL PRIMARY KEY,
            week_start DATE NOT NULL UNIQUE,
            title TEXT NOT NULL,
            mcq_ids JSONB NOT NULL,
            duration_min INTEGER NOT NULL DEFAULT 120,
            created_at TIMESTAMP NOT NULL DEFAULT now()
        );
        CREATE TABLE IF NOT EXISTS weekly_mock_entries (
            id SERIAL PRIMARY KEY,
            mock_id INTEGER NOT NULL REFERENCES weekly_mocks(id) ON DELETE CASCADE,
            user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            started_at TIMESTAMP NOT NULL DEFAULT now(),
            submitted_at TIMESTAMP,
            answers JSONB NOT NULL DEFAULT '{}'::jsonb,
            score INTEGER,
            total INTEGER,
            overtime BOOLEAN NOT NULL DEFAULT false,
            UNIQUE (mock_id, user_id)
        );
        """
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS weekly_mock_entries")
    op.execute("DROP TABLE IF EXISTS weekly_mocks")
    op.execute("DROP TABLE IF EXISTS study_sessions")
    op.execute("DROP INDEX IF EXISTS idx_answer_events_user_mistake")
    op.execute("ALTER TABLE answer_events DROP COLUMN IF EXISTS pair_id")
    op.execute("ALTER TABLE answer_events DROP COLUMN IF EXISTS mistake_type")
    op.execute("DROP TABLE IF EXISTS confusable_pairs")
