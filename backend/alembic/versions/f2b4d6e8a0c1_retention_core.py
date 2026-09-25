"""retention_core

Revision ID: f2b4d6e8a0c1
Revises: e5a7c9d1f3b5
Create Date: 2026-09-25 14:00:00.000000

Daily-loop retention engine:
  concept_cards    one card per tested concept: short explanation, verbatim
                   textbook quote with page, figure, mnemonic, embedding
  concept_reviews  per-user spaced-repetition schedule for each concept
                   (box 0-5, next_due); re-tests use a *different* MCQ
  answer_events    every answered MCQ with the student's confidence
                   (sure / unsure / guess) - feeds scheduling and readiness
  daily_sessions   the assembled "Daily Dose" per user per day (streaks)
  users.exam_date, users.streak_freezes
  mcqs.concept_id  links questions to their concept (new question on re-test)
  mcqs.figure_id   image questions ("spot the diagnosis")
"""

from typing import Sequence, Union

from alembic import op


# revision identifiers, used by Alembic.
revision: str = 'f2b4d6e8a0c1'
down_revision: Union[str, None] = 'e5a7c9d1f3b5'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS concept_cards (
            id SERIAL PRIMARY KEY,
            title TEXT NOT NULL,
            summary TEXT NOT NULL,
            quote TEXT,
            chunk_id INTEGER REFERENCES chunks(id) ON DELETE SET NULL,
            book_title TEXT,
            page_number INTEGER,
            figure_id INTEGER REFERENCES figures(id) ON DELETE SET NULL,
            mnemonic TEXT,
            subject TEXT,
            source TEXT NOT NULL DEFAULT 'textbook',
            grounding TEXT NOT NULL DEFAULT 'textbook',
            embedding vector(1024),
            created_at TIMESTAMP NOT NULL DEFAULT now()
        );
        """
    )
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS concept_reviews (
            id SERIAL PRIMARY KEY,
            user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            concept_id INTEGER NOT NULL REFERENCES concept_cards(id) ON DELETE CASCADE,
            box INTEGER NOT NULL DEFAULT 0,
            next_due TIMESTAMP NOT NULL DEFAULT now(),
            last_result TEXT,
            lapses INTEGER NOT NULL DEFAULT 0,
            reviews INTEGER NOT NULL DEFAULT 0,
            created_at TIMESTAMP NOT NULL DEFAULT now(),
            updated_at TIMESTAMP NOT NULL DEFAULT now(),
            UNIQUE (user_id, concept_id)
        );
        """
    )
    op.execute("CREATE INDEX IF NOT EXISTS idx_concept_reviews_due ON concept_reviews (user_id, next_due);")
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS answer_events (
            id SERIAL PRIMARY KEY,
            user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            mcq_id INTEGER REFERENCES mcqs(id) ON DELETE SET NULL,
            concept_id INTEGER REFERENCES concept_cards(id) ON DELETE SET NULL,
            selected_option TEXT,
            is_correct BOOLEAN NOT NULL,
            confidence TEXT NOT NULL DEFAULT 'sure' CHECK (confidence IN ('sure', 'unsure', 'guess')),
            subject TEXT,
            source TEXT NOT NULL DEFAULT 'quiz',
            created_at TIMESTAMP NOT NULL DEFAULT now()
        );
        """
    )
    op.execute("CREATE INDEX IF NOT EXISTS idx_answer_events_user ON answer_events (user_id, created_at DESC);")
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS daily_sessions (
            id SERIAL PRIMARY KEY,
            user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            day DATE NOT NULL,
            items JSONB NOT NULL DEFAULT '[]'::jsonb,
            completed_at TIMESTAMP,
            freeze_used BOOLEAN NOT NULL DEFAULT false,
            created_at TIMESTAMP NOT NULL DEFAULT now(),
            UNIQUE (user_id, day)
        );
        """
    )
    op.execute("ALTER TABLE users ADD COLUMN IF NOT EXISTS exam_date DATE;")
    op.execute("ALTER TABLE users ADD COLUMN IF NOT EXISTS streak_freezes INTEGER NOT NULL DEFAULT 2;")
    op.execute("ALTER TABLE mcqs ADD COLUMN IF NOT EXISTS concept_id INTEGER REFERENCES concept_cards(id) ON DELETE SET NULL;")
    op.execute("ALTER TABLE mcqs ADD COLUMN IF NOT EXISTS figure_id INTEGER REFERENCES figures(id) ON DELETE SET NULL;")
    op.execute("CREATE INDEX IF NOT EXISTS idx_mcqs_concept ON mcqs (concept_id);")


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS idx_mcqs_concept;")
    op.execute("ALTER TABLE mcqs DROP COLUMN IF EXISTS figure_id;")
    op.execute("ALTER TABLE mcqs DROP COLUMN IF EXISTS concept_id;")
    op.execute("ALTER TABLE users DROP COLUMN IF EXISTS streak_freezes;")
    op.execute("ALTER TABLE users DROP COLUMN IF EXISTS exam_date;")
    op.execute("DROP TABLE IF EXISTS daily_sessions;")
    op.execute("DROP TABLE IF EXISTS answer_events;")
    op.execute("DROP TABLE IF EXISTS concept_reviews;")
    op.execute("DROP TABLE IF EXISTS concept_cards;")
