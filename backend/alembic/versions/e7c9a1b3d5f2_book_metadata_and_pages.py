"""book metadata (authors, edition, year, ISBN, subject) and printed page labels

Revision ID: e7c9a1b3d5f2
Revises: d1e3f5a7b9c2
Create Date: 2026-09-29 20:00:00.000000

Books were named from their file name ("Kaztung Pharmacology") and cited by PDF page, which is 14-20
pages off the printed page in books with front matter. These columns hold what the title and copyright
pages say, plus the printed page label of every PDF page:

    full_title    the title as printed ("Guyton and Hall Textbook of Medical Physiology")
    authors       JSON list of names
    edition       edition number (15 for "Fifteenth Edition")
    year          publication year
    publisher, isbn
    subject       FCPS subject the book serves ("Physiology"); replaces guessing it from the title
    page_labels   JSON list, one printed label per PDF page ("xii", "380"); null where unknown
    aliases       JSON list of earlier titles, so citations saved under an old name still resolve
    meta_source   where the metadata came from ("llm", "manual", ...)

`title` stays the short display name used in citations ("Guyton & Hall 15e").
"""

from typing import Sequence, Union

from alembic import op


# revision identifiers, used by Alembic.
revision: str = 'e7c9a1b3d5f2'
down_revision: Union[str, None] = 'd1e3f5a7b9c2'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("""
        ALTER TABLE books
            ADD COLUMN IF NOT EXISTS full_title TEXT,
            ADD COLUMN IF NOT EXISTS authors JSONB,
            ADD COLUMN IF NOT EXISTS edition INTEGER,
            ADD COLUMN IF NOT EXISTS year INTEGER,
            ADD COLUMN IF NOT EXISTS publisher TEXT,
            ADD COLUMN IF NOT EXISTS isbn TEXT,
            ADD COLUMN IF NOT EXISTS subject TEXT,
            ADD COLUMN IF NOT EXISTS page_labels JSONB,
            ADD COLUMN IF NOT EXISTS aliases JSONB NOT NULL DEFAULT '[]'::jsonb,
            ADD COLUMN IF NOT EXISTS meta_source TEXT
    """)


def downgrade() -> None:
    op.execute("""
        ALTER TABLE books
            DROP COLUMN IF EXISTS full_title, DROP COLUMN IF EXISTS authors, DROP COLUMN IF EXISTS edition,
            DROP COLUMN IF EXISTS year, DROP COLUMN IF EXISTS publisher, DROP COLUMN IF EXISTS isbn,
            DROP COLUMN IF EXISTS subject, DROP COLUMN IF EXISTS page_labels, DROP COLUMN IF EXISTS aliases,
            DROP COLUMN IF EXISTS meta_source
    """)
