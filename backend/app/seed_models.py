"""Seed Studio tables (seed_studio/, migration f7a9b1c3d5e7): Tables of Specifications, seeding jobs and every
question or revision note they produced, rejected ones included.

Questions that pass go into the ordinary `mcqs` table (status 'pending' until an operator approves them, unless the
job publishes on insert); `seed_items` keeps the record of each attempt so the dashboard counts come from rows, not
from counters. Nothing here is read by the learner-facing app.
"""

from datetime import datetime

from sqlalchemy import CheckConstraint, ForeignKey, Index, Text, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.models import Base  # same metadata, so the foreign key to mcqs resolves

NODE_LEVELS = ("family", "paper", "specialty", "subject", "topic", "subtopic")
JOB_STATES = ("draft", "queued", "running", "paused", "cancelling", "cancelled", "completed", "partial", "failed")
BATCH_STATES = ("pending", "running", "done", "failed", "cancelled")
ITEM_STATES = ("rejected", "duplicate", "validated", "inserted")


class SeedTosDocument(Base):
    __tablename__ = "seed_tos_documents"

    id: Mapped[int] = mapped_column(primary_key=True)
    family: Mapped[str] = mapped_column(Text)                 # PGET | FCPS
    title: Mapped[str] = mapped_column(Text)
    source_path: Mapped[str] = mapped_column(Text)            # the PDF, relative to the repo
    source_url: Mapped[str | None] = mapped_column(Text, default=None)
    version: Mapped[str | None] = mapped_column(Text, default=None)   # e.g. "Jan 2027 intake (notified 9 Jul 2026)"
    has_counts: Mapped[bool] = mapped_column(default=True)    # FCPS prospectuses list topics without MCQ counts
    page_count: Mapped[int | None] = mapped_column(default=None)
    parse_status: Mapped[str] = mapped_column(Text, server_default="not_parsed")  # not_parsed|parsing|parsed|failed
    parse_method: Mapped[str | None] = mapped_column(Text, default=None)
    parse_error: Mapped[str | None] = mapped_column(Text, default=None)
    multiplier: Mapped[float] = mapped_column(server_default="5")          # internal bank target x official count
    base_per_topic: Mapped[int] = mapped_column(server_default="2")        # stand-in count where the TOS gives none
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())
    parsed_at: Mapped[datetime | None] = mapped_column(default=None)


class SeedTosPage(Base):
    """What the parser read on one page: kept so a re-parse can resume and an operator can see the source."""

    __tablename__ = "seed_tos_pages"

    id: Mapped[int] = mapped_column(primary_key=True)
    document_id: Mapped[int] = mapped_column(ForeignKey("seed_tos_documents.id", ondelete="CASCADE"))
    page_no: Mapped[int]
    text: Mapped[str | None] = mapped_column(Text, default=None)
    rows: Mapped[list | None] = mapped_column(JSONB, default=None)
    status: Mapped[str] = mapped_column(Text, server_default="pending")    # pending | done | failed | skipped
    error: Mapped[str | None] = mapped_column(Text, default=None)
    model: Mapped[str | None] = mapped_column(Text, default=None)

    __table_args__ = (UniqueConstraint("document_id", "page_no"),)


class SeedTosNode(Base):
    __tablename__ = "seed_tos_nodes"

    id: Mapped[int] = mapped_column(primary_key=True)
    document_id: Mapped[int] = mapped_column(ForeignKey("seed_tos_documents.id", ondelete="CASCADE"))
    parent_id: Mapped[int | None] = mapped_column(ForeignKey("seed_tos_nodes.id", ondelete="CASCADE"), default=None)
    level: Mapped[str] = mapped_column(Text)
    name: Mapped[str] = mapped_column(Text)
    path: Mapped[str] = mapped_column(Text)                   # "PGET > Paper II > Paediatrics > ... "
    position: Mapped[int] = mapped_column(server_default="0")
    official_count: Mapped[int | None] = mapped_column(default=None)       # only what the TOS states
    count_corrected: Mapped[bool] = mapped_column(server_default="false", default=False)  # operator fixed a mis-read
    count_note: Mapped[str | None] = mapped_column(Text, default=None)     # "minimum of two MCQs from each topic"
    detail: Mapped[str | None] = mapped_column(Text, default=None)         # items listed under a heading (migration c8e0a2d4f6b9)
    min_per_child: Mapped[int | None] = mapped_column(default=None)
    target_override: Mapped[int | None] = mapped_column(default=None)      # operator's planned target (audited)
    page_no: Mapped[int | None] = mapped_column(default=None)
    ambiguous: Mapped[bool] = mapped_column(server_default="false")
    ambiguity_note: Mapped[str | None] = mapped_column(Text, default=None)

    __table_args__ = (
        CheckConstraint("level IN ('family','paper','specialty','subject','topic','subtopic')"),
        Index("ix_seed_tos_nodes_doc_parent", "document_id", "parent_id"),
    )


class SeedJob(Base):
    __tablename__ = "seed_jobs"

    id: Mapped[str] = mapped_column(Text, primary_key=True)  # "J" + date + random: idempotent across retries
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())
    created_by: Mapped[str | None] = mapped_column(Text, default=None)
    document_id: Mapped[int] = mapped_column(ForeignKey("seed_tos_documents.id", ondelete="CASCADE"))
    scope_node_id: Mapped[int] = mapped_column(ForeignKey("seed_tos_nodes.id", ondelete="CASCADE"))
    status: Mapped[str] = mapped_column(Text, server_default="draft")
    dry_run: Mapped[bool] = mapped_column(server_default="false")
    config: Mapped[dict] = mapped_column(JSONB, default=dict)
    manifest: Mapped[dict | None] = mapped_column(JSONB, default=None)     # the plan as previewed and confirmed
    started_at: Mapped[datetime | None] = mapped_column(default=None)
    finished_at: Mapped[datetime | None] = mapped_column(default=None)
    error: Mapped[str | None] = mapped_column(Text, default=None)

    __table_args__ = (CheckConstraint(f"status IN {JOB_STATES}"),)


class SeedBatch(Base):
    """One unit of work: N questions on one topic at one difficulty, or one revision note for one leaf."""

    __tablename__ = "seed_batches"

    id: Mapped[int] = mapped_column(primary_key=True)
    job_id: Mapped[str] = mapped_column(ForeignKey("seed_jobs.id", ondelete="CASCADE"))
    node_id: Mapped[int] = mapped_column(ForeignKey("seed_tos_nodes.id", ondelete="CASCADE"))
    kind: Mapped[str] = mapped_column(Text, server_default="mcq")          # mcq | note
    seq: Mapped[int] = mapped_column(server_default="0")
    difficulty: Mapped[int | None] = mapped_column(default=None)
    target: Mapped[int] = mapped_column(server_default="0")
    status: Mapped[str] = mapped_column(Text, server_default="pending")
    stage: Mapped[str | None] = mapped_column(Text, default=None)          # retrieving|generating|validating|...
    attempts: Mapped[int] = mapped_column(server_default="0")
    errors: Mapped[list] = mapped_column(JSONB, default=list, server_default="[]")   # [{at, kind, message}]
    progress: Mapped[dict] = mapped_column(JSONB, default=dict, server_default="{}")  # live counters while running
    tokens_in: Mapped[int] = mapped_column(server_default="0")
    tokens_out: Mapped[int] = mapped_column(server_default="0")
    worker: Mapped[str | None] = mapped_column(Text, default=None)
    started_at: Mapped[datetime | None] = mapped_column(default=None)
    heartbeat_at: Mapped[datetime | None] = mapped_column(default=None)
    finished_at: Mapped[datetime | None] = mapped_column(default=None)

    __table_args__ = (
        UniqueConstraint("job_id", "node_id", "kind", "difficulty", "seq", name="uq_seed_batch_identity"),
        CheckConstraint(f"status IN {BATCH_STATES}"),
        Index("ix_seed_batches_status", "status", "job_id"),
    )


class SeedItem(Base):
    """Every question a batch produced, whatever happened to it."""

    __tablename__ = "seed_items"

    id: Mapped[int] = mapped_column(primary_key=True)
    batch_id: Mapped[int] = mapped_column(ForeignKey("seed_batches.id", ondelete="CASCADE"))
    job_id: Mapped[str] = mapped_column(Text)
    node_id: Mapped[int] = mapped_column(ForeignKey("seed_tos_nodes.id", ondelete="CASCADE"))
    status: Mapped[str] = mapped_column(Text)
    reason: Mapped[str | None] = mapped_column(Text, default=None)         # why rejected / what it duplicates
    difficulty: Mapped[int | None] = mapped_column(default=None)
    subtopic: Mapped[str | None] = mapped_column(Text, default=None)
    payload: Mapped[dict] = mapped_column(JSONB, default=dict)             # stem, options, key, explanation, cites...
    mcq_id: Mapped[int | None] = mapped_column(ForeignKey("mcqs.id", ondelete="SET NULL"), default=None)
    review_state: Mapped[str] = mapped_column(Text, server_default="none")  # none | flagged | approved
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())

    __table_args__ = (
        CheckConstraint(f"status IN {ITEM_STATES}"),
        Index("ix_seed_items_node_status", "node_id", "status"),
        Index("ix_seed_items_job", "job_id"),
    )


class SeedNote(Base):
    """One revision note per TOS leaf (subtopic, or topic when it has none); the latest good one is current."""

    __tablename__ = "seed_notes"

    id: Mapped[int] = mapped_column(primary_key=True)
    node_id: Mapped[int] = mapped_column(ForeignKey("seed_tos_nodes.id", ondelete="CASCADE"))
    job_id: Mapped[str | None] = mapped_column(Text, default=None)
    batch_id: Mapped[int | None] = mapped_column(ForeignKey("seed_batches.id", ondelete="SET NULL"), default=None)
    status: Mapped[str] = mapped_column(Text)                              # done | failed
    markdown: Mapped[str | None] = mapped_column(Text, default=None)
    citations: Mapped[list] = mapped_column(JSONB, default=list, server_default="[]")
    summary_key: Mapped[str | None] = mapped_column(Text, default=None)    # topic_summaries.scope_key
    reason: Mapped[str | None] = mapped_column(Text, default=None)
    review_state: Mapped[str] = mapped_column(Text, server_default="none")
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())

    __table_args__ = (Index("ix_seed_notes_node", "node_id", "status"),)


class SeedAudit(Base):
    __tablename__ = "seed_audit"

    id: Mapped[int] = mapped_column(primary_key=True)
    at: Mapped[datetime] = mapped_column(server_default=func.now())
    actor: Mapped[str | None] = mapped_column(Text, default=None)
    action: Mapped[str] = mapped_column(Text)        # target_change | job_create | job_start | pause | cancel | ...
    entity: Mapped[str] = mapped_column(Text)
    entity_id: Mapped[str] = mapped_column(Text)
    detail: Mapped[dict] = mapped_column(JSONB, default=dict, server_default="{}")
