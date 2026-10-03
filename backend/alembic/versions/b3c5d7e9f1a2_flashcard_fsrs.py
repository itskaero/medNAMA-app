"""FSRS scheduling state on Study Corner flashcards (app/fsrs.py, as for concept reviews)

Revision ID: b3c5d7e9f1a2
Revises: a2b4c6d8e0f1
Create Date: 2026-10-03
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = 'b3c5d7e9f1a2'
down_revision: Union[str, None] = 'a2b4c6d8e0f1'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("flashcards", sa.Column("stability", sa.Float(), nullable=True))
    op.add_column("flashcards", sa.Column("difficulty", sa.Float(), nullable=True))


def downgrade() -> None:
    op.drop_column("flashcards", "difficulty")
    op.drop_column("flashcards", "stability")
