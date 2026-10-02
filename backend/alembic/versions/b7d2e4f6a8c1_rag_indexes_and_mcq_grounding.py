"""rag_indexes_and_mcq_grounding

Revision ID: b7d2e4f6a8c1
Revises: a1b2c3d4e5f6
Create Date: 2026-09-24 12:00:00.000000

Retrieval indexes (the chunks table had none, so every keyword search was a
full scan computing to_tsvector on ~100k rows, ~2 s per query) and the columns
that make AI MCQ generation database-aware:

  mcqs.stem_embedding    bge-large embedding of the stem, for semantic de-dup
                         and "already asked on this topic" lookups
  mcqs.source_chunk_ids  parent chunk ids the question was generated from, so
                         later sets on the same topic rotate to unused passages
  mcqs.tested_concept    short label of the fact tested ("IHPS - metabolic
                         derangement"), shown to the LLM as an exclusion list
  mcqs.grounding         'book' (validated textbook source) or 'ai' (AI clinical
                         knowledge, no textbook page)
"""

from typing import Sequence, Union

from alembic import op


# revision identifiers, used by Alembic.
revision: str = 'b7d2e4f6a8c1'
down_revision: Union[str, None] = 'a1b2c3d4e5f6'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Keyword search runs only over child chunks; the expression must match the
    # one in retrieval.keyword_search exactly for the planner to use it.
    op.execute(
        "CREATE INDEX IF NOT EXISTS idx_chunks_child_fts ON chunks "
        "USING gin (to_tsvector('english', content)) WHERE parent_id IS NOT NULL;"
    )
    op.execute("CREATE INDEX IF NOT EXISTS idx_chunks_parent_id ON chunks (parent_id);")
    op.execute("CREATE INDEX IF NOT EXISTS idx_chunks_book_page ON chunks (book_id, page_number);")
    op.execute("CREATE INDEX IF NOT EXISTS idx_figures_book_page ON figures (book_id, page_number);")

    op.execute("ALTER TABLE mcqs ADD COLUMN IF NOT EXISTS stem_embedding vector(1024);")
    op.execute("ALTER TABLE mcqs ADD COLUMN IF NOT EXISTS source_chunk_ids JSONB;")
    op.execute("ALTER TABLE mcqs ADD COLUMN IF NOT EXISTS tested_concept TEXT;")
    op.execute("ALTER TABLE mcqs ADD COLUMN IF NOT EXISTS grounding TEXT;")


def downgrade() -> None:
    op.execute("ALTER TABLE mcqs DROP COLUMN IF EXISTS grounding;")
    op.execute("ALTER TABLE mcqs DROP COLUMN IF EXISTS tested_concept;")
    op.execute("ALTER TABLE mcqs DROP COLUMN IF EXISTS source_chunk_ids;")
    op.execute("ALTER TABLE mcqs DROP COLUMN IF EXISTS stem_embedding;")
    op.execute("DROP INDEX IF EXISTS idx_figures_book_page;")
    op.execute("DROP INDEX IF EXISTS idx_chunks_book_page;")
    op.execute("DROP INDEX IF EXISTS idx_chunks_parent_id;")
    op.execute("DROP INDEX IF EXISTS idx_chunks_child_fts;")
