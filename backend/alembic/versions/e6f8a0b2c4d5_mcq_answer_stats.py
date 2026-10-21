"""mcqs.answered_n / correct_rate: difficulty measured from students' first answers

Revision ID: e6f8a0b2c4d5
Revises: d5e7f9a1b3c4
Create Date: 2026-10-08
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = 'e6f8a0b2c4d5'
down_revision: Union[str, None] = 'd5e7f9a1b3c4'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('mcqs', sa.Column('answered_n', sa.Integer(), nullable=True))
    op.add_column('mcqs', sa.Column('correct_rate', sa.Float(), nullable=True))


def downgrade() -> None:
    op.drop_column('mcqs', 'correct_rate')
    op.drop_column('mcqs', 'answered_n')
