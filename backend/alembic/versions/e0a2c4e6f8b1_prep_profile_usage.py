"""Learner prep profile (exam paper, daily minutes, onboarding) and anonymous per-screen usage counts

Revision ID: e0a2c4e6f8b1
Revises: d9f1b3c5e7a0
Create Date: 2026-10-20
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision: str = 'e0a2c4e6f8b1'
down_revision: Union[str, None] = 'd9f1b3c5e7a0'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("users", sa.Column("prep_profile", JSONB, nullable=False, server_default="{}"))
    op.create_table(
        "usage_counts",
        sa.Column("view", sa.Text, primary_key=True),
        sa.Column("day", sa.Date, primary_key=True),
        sa.Column("n", sa.Integer, nullable=False, server_default="0"),
    )


def downgrade() -> None:
    op.drop_table("usage_counts")
    op.drop_column("users", "prep_profile")
