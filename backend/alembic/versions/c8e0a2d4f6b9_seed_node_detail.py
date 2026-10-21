"""seed_tos_nodes.detail: the items listed under a TOS heading ("Sensory & Motor" -> "Cervical plexus, Brachial plexus, ...")

Revision ID: c8e0a2d4f6b9
Revises: a1c3e5f7b9d2
Create Date: 2026-10-10
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = 'c8e0a2d4f6b9'
down_revision: Union[str, None] = 'a1c3e5f7b9d2'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('seed_tos_nodes', sa.Column('detail', sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column('seed_tos_nodes', 'detail')
