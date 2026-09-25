"""recall_referee

Revision ID: a9c1e3f5b7d9
Revises: f2b4d6e8a0c1
Create Date: 2026-09-25 17:00:00.000000

Private recall bank + Answer-Key Referee.

  recall_items   question -> published answer pairs extracted from a recall
                 book (never ingested as textbook evidence), plus the referee
                 verdict against the textbooks: supported / contradicted /
                 textbooks_silent / books_conflict, with verbatim quotes
  concept_cards.visibility  'all' | 'admin' - pearls from a private source
                 book stay admin-only until licensed
"""

from typing import Sequence, Union

from alembic import op


# revision identifiers, used by Alembic.
revision: str = 'a9c1e3f5b7d9'
down_revision: Union[str, None] = 'f2b4d6e8a0c1'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS recall_items (
            id SERIAL PRIMARY KEY,
            source TEXT NOT NULL,
            page INTEGER,
            chapter TEXT,
            headline_no INTEGER,
            kind TEXT NOT NULL DEFAULT 'variant',
            question TEXT NOT NULL,
            answer TEXT NOT NULL,
            visibility TEXT NOT NULL DEFAULT 'admin' CHECK (visibility IN ('admin', 'all')),
            verdict TEXT CHECK (verdict IN ('supported', 'contradicted', 'textbooks_silent', 'books_conflict')),
            textbook_answer TEXT,
            evidence JSONB,
            explanation TEXT,
            concept_id INTEGER REFERENCES concept_cards(id) ON DELETE SET NULL,
            mcq_id INTEGER REFERENCES mcqs(id) ON DELETE SET NULL,
            review_status TEXT NOT NULL DEFAULT 'unreviewed'
                CHECK (review_status IN ('unreviewed', 'confirmed', 'corrected', 'rejected')),
            reviewer_note TEXT,
            refereed_at TIMESTAMP,
            created_at TIMESTAMP NOT NULL DEFAULT now(),
            UNIQUE (source, page, question)
        );
        """
    )
    op.execute("CREATE INDEX IF NOT EXISTS idx_recall_items_verdict ON recall_items (source, verdict);")
    op.execute("CREATE INDEX IF NOT EXISTS idx_recall_items_chapter ON recall_items (source, chapter);")
    op.execute("ALTER TABLE concept_cards ADD COLUMN IF NOT EXISTS visibility TEXT NOT NULL DEFAULT 'all';")


def downgrade() -> None:
    op.execute("ALTER TABLE concept_cards DROP COLUMN IF EXISTS visibility;")
    op.execute("DROP TABLE IF EXISTS recall_items;")
