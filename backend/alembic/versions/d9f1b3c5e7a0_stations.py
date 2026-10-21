"""TOACS / viva stations: stations, immutable versions, rubric criteria, attempts, criterion scores,
challenges, expert gold answers, calibration runs and the station audit log

Revision ID: d9f1b3c5e7a0
Revises: c8e0a2d4f6b9
Create Date: 2026-10-14
"""
from typing import Sequence, Union

from alembic import op

revision: str = 'd9f1b3c5e7a0'
down_revision: Union[str, None] = 'c8e0a2d4f6b9'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Defined once in app/station_models.py; created here from that definition (new tables only).
    from app.models import Base
    from app.station_models import TABLES

    Base.metadata.create_all(bind=op.get_bind(), tables=[Base.metadata.tables[t] for t in TABLES])


def downgrade() -> None:
    from app.station_models import TABLES

    op.execute("ALTER TABLE IF EXISTS stations DROP CONSTRAINT IF EXISTS fk_stations_current_version")
    for t in reversed(TABLES):
        op.drop_table(t)
