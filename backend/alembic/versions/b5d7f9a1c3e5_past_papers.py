"""past papers

Revision ID: b5d7f9a1c3e5
Revises: a3c5e7f9b1d2
Create Date: 2026-09-26 16:00:00.000000

  past_papers            one row per exam year (e.g. FCPS Part 1 - 2024), from an imported archive
  past_paper_questions   which bank question appears in which paper, in order
  mcq_tags               every subject / topic / specialty label of a question (a question can
                         carry several, so filters must not rely on sub_category alone)
  mcq_media              question / explanation images, stored in the DB so a dump carries them
  mcqs.source_ref        the importer's external id (idempotent re-imports)
  mcqs.access            'open' | 'restricted'; restricted rows are served only to users allowed
                         by PAST_PAPERS_ACCESS and never in shared features (duels, weekly mock)
"""

from typing import Sequence, Union

from alembic import op


# revision identifiers, used by Alembic.
revision: str = 'b5d7f9a1c3e5'
down_revision: Union[str, None] = 'a3c5e7f9b1d2'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("ALTER TABLE mcqs ADD COLUMN IF NOT EXISTS source_ref TEXT")
    op.execute("ALTER TABLE mcqs ADD COLUMN IF NOT EXISTS access TEXT NOT NULL DEFAULT 'open'")
    op.execute("CREATE UNIQUE INDEX IF NOT EXISTS uq_mcqs_source_ref ON mcqs (source, source_ref) WHERE source_ref IS NOT NULL")
    op.execute("CREATE INDEX IF NOT EXISTS idx_mcqs_access ON mcqs (access) WHERE access <> 'open'")
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS past_papers (
            id SERIAL PRIMARY KEY,
            code TEXT NOT NULL UNIQUE,
            exam TEXT NOT NULL,
            title TEXT NOT NULL,
            year INTEGER,
            source TEXT NOT NULL,
            access TEXT NOT NULL DEFAULT 'restricted',
            created_at TIMESTAMP NOT NULL DEFAULT now()
        );
        CREATE INDEX IF NOT EXISTS idx_past_papers_exam_year ON past_papers (exam, year);
        CREATE TABLE IF NOT EXISTS past_paper_questions (
            paper_id INTEGER NOT NULL REFERENCES past_papers(id) ON DELETE CASCADE,
            mcq_id INTEGER NOT NULL REFERENCES mcqs(id) ON DELETE CASCADE,
            position INTEGER NOT NULL DEFAULT 0,
            PRIMARY KEY (paper_id, mcq_id)
        );
        CREATE INDEX IF NOT EXISTS idx_past_paper_questions_mcq ON past_paper_questions (mcq_id);
        CREATE TABLE IF NOT EXISTS mcq_tags (
            mcq_id INTEGER NOT NULL REFERENCES mcqs(id) ON DELETE CASCADE,
            axis TEXT NOT NULL,
            label TEXT NOT NULL,
            PRIMARY KEY (mcq_id, axis, label)
        );
        CREATE INDEX IF NOT EXISTS idx_mcq_tags_axis_label ON mcq_tags (axis, label);
        CREATE TABLE IF NOT EXISTS mcq_media (
            id SERIAL PRIMARY KEY,
            mcq_id INTEGER NOT NULL REFERENCES mcqs(id) ON DELETE CASCADE,
            role TEXT NOT NULL DEFAULT 'question',
            mime TEXT NOT NULL,
            data BYTEA NOT NULL,
            origin TEXT,
            created_at TIMESTAMP NOT NULL DEFAULT now(),
            UNIQUE (mcq_id, role, origin)
        );
        """
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS mcq_media")
    op.execute("DROP TABLE IF EXISTS mcq_tags")
    op.execute("DROP TABLE IF EXISTS past_paper_questions")
    op.execute("DROP TABLE IF EXISTS past_papers")
    op.execute("DROP INDEX IF EXISTS idx_mcqs_access")
    op.execute("DROP INDEX IF EXISTS uq_mcqs_source_ref")
    op.execute("ALTER TABLE mcqs DROP COLUMN IF EXISTS access")
    op.execute("ALTER TABLE mcqs DROP COLUMN IF EXISTS source_ref")
