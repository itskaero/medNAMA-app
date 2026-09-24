"""chunk_vector_index

Revision ID: e5a7c9d1f3b5
Revises: d4f6b8c0e2a3
Create Date: 2026-09-24 22:00:00.000000

HNSW index for child-chunk vector search. Without it every question scanned
all ~105k child embeddings (~400 MB); on the NAS, whose RAM cannot cache that,
this cost ~10 s per query from disk. With the index a query reads a few
hundred vectors.

Building the index takes minutes on a slow CPU, and this migration runs at
backend start-up. mednama-nas-deploy/deploy.sh builds it beforehand with
CREATE INDEX CONCURRENTLY while the old app keeps serving, so this migration is
then a no-op. (Single-process build: Docker's 64 MB /dev/shm is too small for
parallel index builds.)
"""

from typing import Sequence, Union

from alembic import op


# revision identifiers, used by Alembic.
revision: str = 'e5a7c9d1f3b5'
down_revision: Union[str, None] = 'd4f6b8c0e2a3'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute(
        "CREATE INDEX IF NOT EXISTS idx_chunks_child_embedding_hnsw ON chunks "
        "USING hnsw (embedding vector_cosine_ops) WHERE parent_id IS NOT NULL;"
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS idx_chunks_child_embedding_hnsw;")
