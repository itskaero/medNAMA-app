"""FCPS Part 1 Paper 1 / Paper 2 (not "FCPS Part 1" / "FCPS Part 2")

Revision ID: f3b5d7e9a1c4
Revises: e1a3c5b7d9f2
Create Date: 2026-09-28 18:00:00.000000

The seeded banks were labelled by their folder names p1/p2 as "FCPS Part 1" and "FCPS Part 2". They are the
two papers of the FCPS Part 1 exam: Paper 1, basic sciences common to every faculty, and Paper 2, the faculty
paper (Medicine, Surgery, Gynae & Obs, Radiology, ENT, Eye, Psychiatry, Anaesthesia). FCPS Part 2 is a
different exam that medNAMA does not cover. Weekly mock titles are renamed the same way.
"""

from typing import Sequence, Union

from alembic import op


# revision identifiers, used by Alembic.
revision: str = 'f3b5d7e9a1c4'
down_revision: Union[str, None] = 'e1a3c5b7d9f2'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

CATEGORIES = (("FCPS Part 1", "Paper 1 · Basic sciences"), ("FCPS Part 2", "Paper 2 · Faculty"))
TITLES = (("FCPS Part 1", "Paper 1"), ("FCPS Part 2", "Paper 2"), ("all specialties", "all faculties"))


def _rename(categories, titles) -> None:
    for old, new in categories:
        op.execute(f"UPDATE mcqs SET main_category = '{new}' WHERE main_category = '{old}'")
    for old, new in titles:   # weekly papers only; personal timed papers are titled after past-paper exams
        op.execute(f"UPDATE weekly_mocks SET title = replace(title, '{old}', '{new}') "
                   f"WHERE part IN ('p1', 'p2') AND title LIKE '%{old}%'")


def upgrade() -> None:
    _rename(CATEGORIES, TITLES)


def downgrade() -> None:
    _rename([(new, old) for old, new in CATEGORIES], [(new, old) for old, new in TITLES])
