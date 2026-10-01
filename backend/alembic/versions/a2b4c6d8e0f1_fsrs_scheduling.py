"""FSRS scheduling state on concept reviews (app/fsrs.py)

Revision ID: a2b4c6d8e0f1
Revises: f1a3c5e7b9d2
Create Date: 2026-10-01
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = 'a2b4c6d8e0f1'
down_revision: Union[str, None] = 'f1a3c5e7b9d2'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("concept_reviews", sa.Column("stability", sa.Float(), nullable=True))
    op.add_column("concept_reviews", sa.Column("difficulty", sa.Float(), nullable=True))
    op.add_column("concept_reviews", sa.Column("last_review_at", sa.DateTime(), nullable=True))


def downgrade() -> None:
    op.drop_column("concept_reviews", "last_review_at")
    op.drop_column("concept_reviews", "difficulty")
    op.drop_column("concept_reviews", "stability")
