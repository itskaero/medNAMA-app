"""PaedNAMA's own tables (all named paeds_*). They live in medNAMA's database and metadata, so foreign keys to users
and books work and a deleted account takes its PaedNAMA rows with it; medNAMA's own migrations never see them.
Merge back: move these into backend/app with an Alembic migration when PaedNAMA joins medNAMA.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import Column, Date, DateTime, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB

from . import settings  # noqa: F401  (backend/ on the path)
from app.models import Base  # noqa: E402


class PaedsProfile(Base):
    """What the learner is preparing for: the CPSP stage, the sitting, where they are in training."""
    __tablename__ = "paeds_profile"
    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), primary_key=True)
    stage = Column(String(10), nullable=False, default="imm")        # imm | fcps2
    exam_date = Column(Date)
    training_year = Column(Integer)                                   # 1-4
    rotation = Column(String(80))                                     # e.g. "Neonatology"
    minutes_per_day = Column(Integer, nullable=False, default=60)
    updated_at = Column(DateTime, nullable=False, default=datetime.utcnow, onupdate=datetime.utcnow)


class PaedsBookRole(Base):
    """What a library book is for in PaedNAMA: the spine everyone reads first, the reference, a specialty book."""
    __tablename__ = "paeds_book_roles"
    book_id = Column(Integer, ForeignKey("books.id", ondelete="CASCADE"), primary_key=True)
    role = Column(String(30), nullable=False)
    note = Column(Text)


class PaedsSheet(Base):
    """A topic one-pager, written once from the books and kept. book_id null = all Paediatrics books; a book id =
    that book's own account of the topic (the "book lens")."""
    __tablename__ = "paeds_sheets"
    id = Column(Integer, primary_key=True)
    stage = Column(String(10), nullable=False)
    topic_id = Column(Text, nullable=False)
    lens = Column(String(20), nullable=False)          # "all" | str(book_id)
    markdown = Column(Text, nullable=False)
    citations = Column(JSONB, nullable=False, default=list)
    version = Column(Integer, nullable=False, default=1)   # the prompt it was written with; older ones are rewritten
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)
    __table_args__ = (UniqueConstraint("stage", "topic_id", "lens", name="uq_paeds_sheet"),)


class PaedsReadPack(Base):
    """The reading loop's material for a topic, prepared once: the opening case, the sections to read (passages with
    their book and page) and the recall questions for each section (each answer backed by a verbatim quote)."""
    __tablename__ = "paeds_read_packs"
    id = Column(Integer, primary_key=True)
    stage = Column(String(10), nullable=False)
    topic_id = Column(Text, nullable=False)
    case = Column(JSONB, nullable=False)
    sections = Column(JSONB, nullable=False)
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)
    __table_args__ = (UniqueConstraint("stage", "topic_id", name="uq_paeds_pack"),)


class PaedsReading(Base):
    """One learner's progress through a topic's reading loop."""
    __tablename__ = "paeds_reading"
    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), primary_key=True)
    stage = Column(String(10), primary_key=True)
    topic_id = Column(Text, primary_key=True)
    case_answer = Column(Text)
    sections_done = Column(JSONB, nullable=False, default=list)
    recall_correct = Column(Integer, nullable=False, default=0)
    recall_total = Column(Integer, nullable=False, default=0)
    explain_scores = Column(JSONB, nullable=False, default=dict)    # section index -> 0-100
    cards_added = Column(Integer, nullable=False, default=0)
    finished_at = Column(DateTime)
    updated_at = Column(DateTime, nullable=False, default=datetime.utcnow, onupdate=datetime.utcnow)


class PaedsChat(Base):
    """A conversation with Dr. Shifa (explain, tutor or clinical reasoning), optionally about one syllabus topic."""
    __tablename__ = "paeds_chats"
    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    title = Column(Text, nullable=False)
    mode = Column(String(12), nullable=False, default="explain")      # explain | tutor | reasoning
    stage = Column(String(10), nullable=False, default="imm")
    topic_id = Column(Text)
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)
    updated_at = Column(DateTime, nullable=False, default=datetime.utcnow, onupdate=datetime.utcnow)


class PaedsChatMessage(Base):
    __tablename__ = "paeds_chat_messages"
    id = Column(Integer, primary_key=True)
    chat_id = Column(Integer, ForeignKey("paeds_chats.id", ondelete="CASCADE"), nullable=False, index=True)
    role = Column(String(10), nullable=False)          # user | shifa
    content = Column(Text, nullable=False)
    citations = Column(JSONB, nullable=False, default=list)
    grounding = Column(String(20))
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)


class PaedsTopicMcq(Base):
    """A question filed under a syllabus topic: written for it on demand (Practise) or by a Seed Studio batch. The
    question itself is medNAMA's (mcqs), so answers, explanations and review cards work as everywhere else."""
    __tablename__ = "paeds_topic_mcqs"
    stage = Column(String(10), primary_key=True)
    topic_id = Column(String(200), primary_key=True)
    mcq_id = Column(Integer, ForeignKey("mcqs.id", ondelete="CASCADE"), primary_key=True, index=True)
    source = Column(String(20), nullable=False, default="practice")   # practice | seed
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)


TABLES = [PaedsProfile.__table__, PaedsBookRole.__table__, PaedsSheet.__table__, PaedsReadPack.__table__,
          PaedsReading.__table__, PaedsChat.__table__, PaedsChatMessage.__table__, PaedsTopicMcq.__table__]


def create_tables() -> None:
    from app.database import engine

    Base.metadata.create_all(engine, tables=TABLES)
    from sqlalchemy import text

    with engine.begin() as conn:      # columns added after a table first existed (create_all never alters)
        conn.execute(text("ALTER TABLE paeds_sheets ADD COLUMN IF NOT EXISTS version integer NOT NULL DEFAULT 1"))
