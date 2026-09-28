"""recall groups and past-paper twists

Revision ID: e1a3c5b7d9f2
Revises: d9f1b3c5e7a9
Create Date: 2026-09-28 02:00:00.000000

recall_group: past-paper questions from different archives (Radiant, MediVerse) that recall the same
exam question in other words are linked, not merged (scripts/link_recalls.py). Each keeps its own
wording and key; a session shows one per group and "seen" spans the group.

twist_of: an AI question written from a past-paper question (same concept, a different ask),
grounded in the textbooks and checked by the Answer-Key Referee (app/twists.py).
"""

from typing import Sequence, Union

from alembic import op


# revision identifiers, used by Alembic.
revision: str = 'e1a3c5b7d9f2'
down_revision: Union[str, None] = 'd9f1b3c5e7a9'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("ALTER TABLE mcqs ADD COLUMN IF NOT EXISTS recall_group INTEGER")
    op.execute("ALTER TABLE mcqs ADD COLUMN IF NOT EXISTS twist_of INTEGER REFERENCES mcqs(id) ON DELETE CASCADE")
    op.execute("CREATE INDEX IF NOT EXISTS ix_mcqs_recall_group ON mcqs (recall_group) WHERE recall_group IS NOT NULL")
    op.execute("CREATE INDEX IF NOT EXISTS ix_mcqs_twist_of ON mcqs (twist_of) WHERE twist_of IS NOT NULL")


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_mcqs_twist_of")
    op.execute("DROP INDEX IF EXISTS ix_mcqs_recall_group")
    op.execute("ALTER TABLE mcqs DROP COLUMN IF EXISTS twist_of")
    op.execute("ALTER TABLE mcqs DROP COLUMN IF EXISTS recall_group")
