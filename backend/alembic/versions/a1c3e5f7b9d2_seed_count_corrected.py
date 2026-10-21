"""seed_tos_nodes.count_corrected: an operator's correction of a mis-read count survives re-reading the TOS

Revision ID: a1c3e5f7b9d2
Revises: f7a9b1c3d5e7
Create Date: 2026-10-09
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = 'a1c3e5f7b9d2'
down_revision: Union[str, None] = 'f7a9b1c3d5e7'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('seed_tos_nodes', sa.Column('count_corrected', sa.Boolean(), server_default='false', nullable=False))


def downgrade() -> None:
    op.drop_column('seed_tos_nodes', 'count_corrected')
