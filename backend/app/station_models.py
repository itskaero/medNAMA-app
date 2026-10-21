"""TOACS / viva stations (revision.md Parts III–IV; migration d9f1b3c5e7a0).

A station is a reusable examination task; its content lives in immutable versions so a published change
never alters an attempt already sat against an earlier version (Q20, §16). Learners only ever see stations
whose status is 'published'. Assessment readiness is separate from publication: a published station stays
'practice_only' until its AI scorer agrees with expert-marked gold answers (station_calibration_runs).

Specialty and blueprint come from the TOS registry (tos_exam_id / tos_paper_id / tos_topic_id), never from
values invented here.
"""

from datetime import datetime

from sqlalchemy import CheckConstraint, ForeignKey, Index, Text, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.models import Base

STATION_STATES = ("draft", "in_review", "approved", "published", "retired")
READINESS = ("practice_only", "assessment_ready")
STATION_TYPES = ("viva", "data_interpretation", "management")
COMPETENCIES = ("knowledge", "reasoning", "prioritisation", "communication", "practical", "safety")
MODES = ("guided", "timed")
PERSONAS = ("tutor", "examiner", "strict")
PRESSURES = ("calm", "realistic", "high")
ATTEMPT_STATES = ("active", "submitted", "scoring_pending", "scored")
UNCERTAINTY = ("none", "low", "high")
ERROR_CLASSES = ("knowledge_gap", "reasoning", "prioritisation", "communication", "practical", "safety", "time")
GOLD_LABELS = ("strong", "borderline", "unsafe")


def _in(col: str, values: tuple[str, ...]) -> str:
    return f"{col} IN ({', '.join(repr(v) for v in values)})"


class Station(Base):
    __tablename__ = "stations"

    id: Mapped[int] = mapped_column(primary_key=True)
    specialty: Mapped[str] = mapped_column(Text)                       # e.g. "Paediatrics"
    tos_exam_id: Mapped[str | None] = mapped_column(Text, default=None)
    tos_paper_id: Mapped[str | None] = mapped_column(Text, default=None)
    tos_topic_id: Mapped[str | None] = mapped_column(Text, default=None)
    title: Mapped[str] = mapped_column(Text)
    station_type: Mapped[str] = mapped_column(Text, server_default="viva")
    status: Mapped[str] = mapped_column(Text, server_default="draft")
    readiness: Mapped[str] = mapped_column(Text, server_default="practice_only")
    current_version_id: Mapped[int | None] = mapped_column(
        ForeignKey("station_versions.id", use_alter=True, name="fk_stations_current_version", ondelete="SET NULL"),
        default=None)
    provenance: Mapped[str] = mapped_column(Text, server_default="ai_draft")   # ai_draft | authored
    created_by: Mapped[str | None] = mapped_column(Text, default=None)
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(server_default=func.now())
    published_at: Mapped[datetime | None] = mapped_column(default=None)

    __table_args__ = (
        CheckConstraint(_in("status", STATION_STATES), name="ck_stations_status"),
        CheckConstraint(_in("readiness", READINESS), name="ck_stations_readiness"),
        CheckConstraint(_in("station_type", STATION_TYPES), name="ck_stations_type"),
        Index("ix_stations_topic", "tos_exam_id", "tos_paper_id", "tos_topic_id"),
    )


class StationVersion(Base):
    """One immutable snapshot of a station's content. Editing an approved version creates a new one."""

    __tablename__ = "station_versions"

    id: Mapped[int] = mapped_column(primary_key=True)
    station_id: Mapped[int] = mapped_column(ForeignKey("stations.id", ondelete="CASCADE"))
    version: Mapped[int] = mapped_column()
    scenario: Mapped[str] = mapped_column(Text)                         # opening stem shown to the candidate
    stages: Mapped[list] = mapped_column(JSONB, default=list)           # [{"after_item": n, "text": "..."}] staged info
    instructions_guided: Mapped[str] = mapped_column(Text, server_default="")
    instructions_timed: Mapped[str] = mapped_column(Text, server_default="")   # never contains answers (Q22)
    framework: Mapped[list] = mapped_column(JSONB, default=list)        # [{"id","prompt","competency","follow_ups":[...]}]
    response_formats: Mapped[list] = mapped_column(JSONB, default=lambda: ["text"])
    time_limit_s: Mapped[int] = mapped_column(server_default="480")
    difficulty: Mapped[int] = mapped_column(server_default="3")
    difficulty_dims: Mapped[dict] = mapped_column(JSONB, default=dict)
    model_answer: Mapped[str] = mapped_column(Text, server_default="")
    pass_mark: Mapped[float | None] = mapped_column(default=None)       # only a rubric-defined threshold allows pass/fail
    sources: Mapped[list] = mapped_column(JSONB, default=list)          # [{"n","book_title","page_number","chapter"}]
    lineage: Mapped[dict] = mapped_column(JSONB, default=dict)          # parent_version_id, prompt_hash, model, job
    review_notes: Mapped[str | None] = mapped_column(Text, default=None)
    created_by: Mapped[str | None] = mapped_column(Text, default=None)
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())
    approved_by: Mapped[str | None] = mapped_column(Text, default=None)
    approved_at: Mapped[datetime | None] = mapped_column(default=None)   # set => frozen

    __table_args__ = (UniqueConstraint("station_id", "version", name="uq_station_version"),)


class StationCriterion(Base):
    __tablename__ = "station_rubric_criteria"

    id: Mapped[int] = mapped_column(primary_key=True)
    version_id: Mapped[int] = mapped_column(ForeignKey("station_versions.id", ondelete="CASCADE"))
    ordinal: Mapped[int] = mapped_column()
    competency: Mapped[str] = mapped_column(Text)
    expected: Mapped[str] = mapped_column(Text)                         # the behaviour that earns the marks
    alternatives: Mapped[list] = mapped_column(JSONB, default=list)     # acceptable alternative answers (Q6)
    marks: Mapped[float] = mapped_column()
    safety_critical: Mapped[bool] = mapped_column(server_default="false")
    prompted_credit: Mapped[float] = mapped_column(server_default="0.5")   # share of marks if earned after a hint
    source_ref: Mapped[dict] = mapped_column(JSONB, default=dict)       # {"n","book_title","page_number","quote"}
    flagged: Mapped[bool] = mapped_column(server_default="false")       # draft check: citation not verified

    __table_args__ = (
        CheckConstraint(_in("competency", COMPETENCIES), name="ck_station_criteria_competency"),
        Index("ix_station_criteria_version", "version_id", "ordinal"),
    )


class StationAttempt(Base):
    __tablename__ = "station_attempts"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    station_id: Mapped[int] = mapped_column(ForeignKey("stations.id", ondelete="CASCADE"))
    version_id: Mapped[int] = mapped_column(ForeignKey("station_versions.id", ondelete="CASCADE"))   # pinned
    mode: Mapped[str] = mapped_column(Text)
    persona: Mapped[str] = mapped_column(Text, server_default="examiner")
    pressure: Mapped[str] = mapped_column(Text, server_default="realistic")
    state: Mapped[str] = mapped_column(Text, server_default="active")
    started_at: Mapped[datetime] = mapped_column(server_default=func.now())
    deadline: Mapped[datetime | None] = mapped_column(default=None)    # timed mode: server clock, never extended
    submitted_at: Mapped[datetime | None] = mapped_column(default=None)
    scored_at: Mapped[datetime | None] = mapped_column(default=None)
    transcript: Mapped[list] = mapped_column(JSONB, default=list)       # [{"role","text","item","confidence","hint",...}]
    framework_pos: Mapped[int] = mapped_column(server_default="0")
    hints_used: Mapped[int] = mapped_column(server_default="0")
    events: Mapped[list] = mapped_column(JSONB, default=list)           # interruptions, recovery, timeouts
    marks_available: Mapped[float | None] = mapped_column(default=None)
    marks_awarded: Mapped[float | None] = mapped_column(default=None)
    result_note: Mapped[str | None] = mapped_column(Text, default=None)
    scorer_meta: Mapped[dict] = mapped_column(JSONB, default=dict)      # prompt hash, model, gold examples used

    __table_args__ = (
        CheckConstraint(_in("mode", MODES), name="ck_station_attempts_mode"),
        CheckConstraint(_in("persona", PERSONAS), name="ck_station_attempts_persona"),
        CheckConstraint(_in("pressure", PRESSURES), name="ck_station_attempts_pressure"),
        CheckConstraint(_in("state", ATTEMPT_STATES), name="ck_station_attempts_state"),
        Index("ix_station_attempts_user", "user_id", "started_at"),
    )


class StationScore(Base):
    """One criterion's mark for an attempt, with the evidence and rationale behind it (§20)."""

    __tablename__ = "station_scores"

    id: Mapped[int] = mapped_column(primary_key=True)
    attempt_id: Mapped[int] = mapped_column(ForeignKey("station_attempts.id", ondelete="CASCADE"))
    criterion_id: Mapped[int] = mapped_column(ForeignKey("station_rubric_criteria.id", ondelete="CASCADE"))
    marks_available: Mapped[float] = mapped_column()
    marks_awarded: Mapped[float] = mapped_column()
    evidence: Mapped[str | None] = mapped_column(Text, default=None)   # verbatim quote from the transcript
    rationale: Mapped[str] = mapped_column(Text, server_default="")
    uncertainty: Mapped[str] = mapped_column(Text, server_default="none")
    flagged: Mapped[bool] = mapped_column(server_default="false")      # needs human review
    prompted: Mapped[bool] = mapped_column(server_default="false")     # earned only after a hint
    unsafe: Mapped[bool] = mapped_column(server_default="false")       # an unsafe action, not just an omission
    error_class: Mapped[str | None] = mapped_column(Text, default=None)

    __table_args__ = (CheckConstraint(_in("uncertainty", UNCERTAINTY), name="ck_station_scores_uncertainty"),
                      Index("ix_station_scores_attempt", "attempt_id"))


class StationChallenge(Base):
    """A learner's challenge of one criterion (Q25). The original score row is never changed."""

    __tablename__ = "station_challenges"

    id: Mapped[int] = mapped_column(primary_key=True)
    attempt_id: Mapped[int] = mapped_column(ForeignKey("station_attempts.id", ondelete="CASCADE"))
    score_id: Mapped[int] = mapped_column(ForeignKey("station_scores.id", ondelete="CASCADE"))
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    rationale: Mapped[str] = mapped_column(Text)
    source: Mapped[str | None] = mapped_column(Text, default=None)
    status: Mapped[str] = mapped_column(Text, server_default="open")   # open | upheld | rejected
    reviewer: Mapped[str | None] = mapped_column(Text, default=None)
    reviewer_note: Mapped[str | None] = mapped_column(Text, default=None)
    corrected_marks: Mapped[float | None] = mapped_column(default=None)
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())
    resolved_at: Mapped[datetime | None] = mapped_column(default=None)

    __table_args__ = (CheckConstraint("status IN ('open', 'upheld', 'rejected')", name="ck_station_challenges_status"),)


class StationGold(Base):
    """An expert-marked candidate response: the standard the AI scorer is calibrated against and shown as an
    example of. The AI may write the sample answer, never its marks."""

    __tablename__ = "station_gold_responses"

    id: Mapped[int] = mapped_column(primary_key=True)
    version_id: Mapped[int] = mapped_column(ForeignKey("station_versions.id", ondelete="CASCADE"))
    label: Mapped[str] = mapped_column(Text)
    transcript: Mapped[list] = mapped_column(JSONB, default=list)
    expert_marks: Mapped[dict] = mapped_column(JSONB, default=dict)    # {criterion_id: marks}; empty until marked
    marked_by: Mapped[str | None] = mapped_column(Text, default=None)
    marked_at: Mapped[datetime | None] = mapped_column(default=None)
    origin: Mapped[str] = mapped_column(Text, server_default="ai_sample")   # ai_sample | challenge | correction | authored
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())

    __table_args__ = (CheckConstraint(_in("label", GOLD_LABELS), name="ck_station_gold_label"),)


class StationCalibration(Base):
    """The AI scorer run over a version's marked gold answers, and how closely it agreed with the expert."""

    __tablename__ = "station_calibration_runs"

    id: Mapped[int] = mapped_column(primary_key=True)
    version_id: Mapped[int] = mapped_column(ForeignKey("station_versions.id", ondelete="CASCADE"))
    at: Mapped[datetime] = mapped_column(server_default=func.now())
    scorer_hash: Mapped[str] = mapped_column(Text)
    n_gold: Mapped[int] = mapped_column()
    metrics: Mapped[dict] = mapped_column(JSONB, default=dict)   # mae, exact_rate, safety_misses, false_flags, per_criterion
    thresholds: Mapped[dict] = mapped_column(JSONB, default=dict)
    passed: Mapped[bool] = mapped_column(server_default="false")
    run_by: Mapped[str | None] = mapped_column(Text, default=None)


class StationAudit(Base):
    """Every change to station content, review state, publication, scores and challenges (Q20)."""

    __tablename__ = "station_audit"

    id: Mapped[int] = mapped_column(primary_key=True)
    at: Mapped[datetime] = mapped_column(server_default=func.now())
    actor: Mapped[str | None] = mapped_column(Text, default=None)
    action: Mapped[str] = mapped_column(Text)
    entity: Mapped[str] = mapped_column(Text)
    entity_id: Mapped[str] = mapped_column(Text)
    detail: Mapped[dict] = mapped_column(JSONB, default=dict, server_default="{}")


TABLES = ("stations", "station_versions", "station_rubric_criteria", "station_attempts", "station_scores",
          "station_challenges", "station_gold_responses", "station_calibration_runs", "station_audit")
