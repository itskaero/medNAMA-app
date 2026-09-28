"""SQLAlchemy models matching schema.sql."""

from datetime import date, datetime

from pgvector.sqlalchemy import Vector
from sqlalchemy import CheckConstraint, ForeignKey, LargeBinary, Text, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    pass


class Book(Base):
    __tablename__ = "books"

    id: Mapped[int] = mapped_column(primary_key=True)
    title: Mapped[str] = mapped_column(Text)
    filename: Mapped[str] = mapped_column(Text, unique=True)
    status: Mapped[str] = mapped_column(Text, server_default="pending")
    error_message: Mapped[str | None] = mapped_column(Text, default=None)
    total_pages: Mapped[int | None] = mapped_column(default=None)
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(server_default=func.now(), onupdate=func.now())

    chunks: Mapped[list["Chunk"]] = relationship(
        back_populates="book", cascade="all, delete-orphan", passive_deletes=True
    )
    figures: Mapped[list["Figure"]] = relationship(
        back_populates="book", cascade="all, delete-orphan", passive_deletes=True
    )

    __table_args__ = (
        CheckConstraint("status IN ('pending', 'processing', 'ready', 'failed')"),
    )


class Chunk(Base):
    __tablename__ = "chunks"

    id: Mapped[int] = mapped_column(primary_key=True)
    book_id: Mapped[int] = mapped_column(ForeignKey("books.id", ondelete="CASCADE"))
    chapter: Mapped[str | None] = mapped_column(Text, default=None)
    page_number: Mapped[int | None] = mapped_column(default=None)
    content: Mapped[str] = mapped_column(Text)
    embedding: Mapped[list[float] | None] = mapped_column(Vector(1024), default=None)
    parent_id: Mapped[int | None] = mapped_column(ForeignKey("chunks.id", ondelete="CASCADE"), default=None)
    extra_metadata: Mapped[dict | None] = mapped_column(JSONB, default=None)
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())

    book: Mapped["Book"] = relationship(back_populates="chunks")


class Figure(Base):
    __tablename__ = "figures"

    id: Mapped[int] = mapped_column(primary_key=True)
    book_id: Mapped[int] = mapped_column(ForeignKey("books.id", ondelete="CASCADE"))
    figure_label: Mapped[str | None] = mapped_column(Text, default=None)
    caption: Mapped[str | None] = mapped_column(Text, default=None)
    page_number: Mapped[int | None] = mapped_column(default=None)
    image_data: Mapped[bytes] = mapped_column(LargeBinary, deferred=True)
    mime_type: Mapped[str] = mapped_column(Text, server_default="image/png")
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())
    # Caption backfill (migration d4f6b8c0e2a3)
    width: Mapped[int | None] = mapped_column(default=None)
    height: Mapped[int | None] = mapped_column(default=None)
    is_decorative: Mapped[bool | None] = mapped_column(default=None)
    caption_source: Mapped[str | None] = mapped_column(Text, default=None)  # 'printed'
    caption_embedding: Mapped[list[float] | None] = mapped_column(Vector(1024), default=None, deferred=True)

    book: Mapped["Book"] = relationship(back_populates="figures")


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(primary_key=True)
    username: Mapped[str] = mapped_column(Text, unique=True)
    password_hash: Mapped[str] = mapped_column(Text)
    role: Mapped[str] = mapped_column(Text, server_default="student")
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())
    # Daily loop (migration f2b4d6e8a0c1)
    exam_date: Mapped[date | None] = mapped_column(default=None)
    streak_freezes: Mapped[int] = mapped_column(server_default="2")

    attempts: Mapped[list["QuizAttempt"]] = relationship(back_populates="user", cascade="all, delete-orphan")
    conversations: Mapped[list["ChatConversation"]] = relationship(back_populates="user", cascade="all, delete-orphan")

    __table_args__ = (
        CheckConstraint("role IN ('admin', 'student')"),
    )


class MCQ(Base):
    __tablename__ = "mcqs"

    id: Mapped[int] = mapped_column(primary_key=True)
    book_id: Mapped[int | None] = mapped_column(ForeignKey("books.id"), default=None)
    quiz_set_id: Mapped[str | None] = mapped_column(Text, default=None)
    quiz_set_title: Mapped[str | None] = mapped_column(Text, default=None)
    question_text: Mapped[str] = mapped_column(Text)
    options: Mapped[dict] = mapped_column(JSONB)
    correct_option: Mapped[str] = mapped_column(Text)
    topic: Mapped[str | None] = mapped_column(Text, default=None)
    main_category: Mapped[str | None] = mapped_column(Text, default=None)
    sub_category: Mapped[str | None] = mapped_column(Text, default=None)
    explanation_markdown: Mapped[str | None] = mapped_column(Text, default=None)
    explanation_citations: Mapped[dict | None] = mapped_column(JSONB, default=None)
    explanation_figures: Mapped[dict | None] = mapped_column(JSONB, default=None)
    status: Mapped[str] = mapped_column(Text, server_default="pending")
    difficulty: Mapped[int | None] = mapped_column(default=None)  # 1 (easiest) - 5 (hardest), None = unspecified
    error_message: Mapped[str | None] = mapped_column(Text, default=None)
    # Database-aware generation (migration b7d2e4f6a8c1)
    stem_embedding: Mapped[list[float] | None] = mapped_column(Vector(1024), default=None, deferred=True)
    source_chunk_ids: Mapped[list | None] = mapped_column(JSONB, default=None)
    tested_concept: Mapped[str | None] = mapped_column(Text, default=None)
    grounding: Mapped[str | None] = mapped_column(Text, default=None)  # 'book' | 'ai'
    concept_id: Mapped[int | None] = mapped_column(ForeignKey("concept_cards.id", ondelete="SET NULL"), default=None)
    figure_id: Mapped[int | None] = mapped_column(ForeignKey("figures.id", ondelete="SET NULL"), default=None)
    source: Mapped[str | None] = mapped_column(Text, default=None)   # e.g. 'seed:p1/patho.js' (scripts/seed_mcqs.py)
    source_ref: Mapped[str | None] = mapped_column(Text, default=None)   # importer's external id
    access: Mapped[str] = mapped_column(Text, server_default="open")      # open | restricted (PAST_PAPERS_ACCESS)
    asked_years: Mapped[list | None] = mapped_column(JSONB, default=None)  # incl. reworded repeats (rank_past_papers.py)
    recall_group: Mapped[int | None] = mapped_column(default=None)  # same recalled question across archives (link_recalls.py)
    twist_of: Mapped[int | None] = mapped_column(ForeignKey("mcqs.id", ondelete="CASCADE"), default=None)  # app/twists.py

    book: Mapped["Book | None"] = relationship()

    __table_args__ = (
        CheckConstraint("status IN ('pending', 'generating', 'ready', 'failed')"),
        CheckConstraint("difficulty IS NULL OR difficulty BETWEEN 1 AND 5"),
    )


class QuizAttempt(Base):
    __tablename__ = "quiz_attempts"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    started_at: Mapped[datetime] = mapped_column(server_default=func.now())
    completed_at: Mapped[datetime | None] = mapped_column(default=None)
    score: Mapped[int | None] = mapped_column(default=None)
    total_questions: Mapped[int | None] = mapped_column(default=None)
    timer_mode: Mapped[str] = mapped_column(Text, server_default="none")
    timer_value: Mapped[int | None] = mapped_column(default=None)
    feedback_mode: Mapped[str] = mapped_column(Text, server_default="tutor")
    label: Mapped[str | None] = mapped_column(Text, default=None)   # what the session was started as (Stats)

    user: Mapped["User"] = relationship(back_populates="attempts")
    answers: Mapped[list["AttemptAnswer"]] = relationship(back_populates="attempt", cascade="all, delete-orphan")


class AttemptAnswer(Base):
    __tablename__ = "attempt_answers"

    id: Mapped[int] = mapped_column(primary_key=True)
    quiz_attempt_id: Mapped[int] = mapped_column(ForeignKey("quiz_attempts.id"))
    mcq_id: Mapped[int] = mapped_column(ForeignKey("mcqs.id"))
    selected_option: Mapped[str] = mapped_column(Text)
    is_correct: Mapped[bool]

    attempt: Mapped["QuizAttempt"] = relationship(back_populates="answers")
    mcq: Mapped["MCQ"] = relationship()


class ChatConversation(Base):
    __tablename__ = "chat_conversations"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    title: Mapped[str] = mapped_column(Text, default="New Conversation")
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(server_default=func.now(), onupdate=func.now())

    user: Mapped["User"] = relationship(back_populates="conversations")
    messages: Mapped[list["ChatMessage"]] = relationship(back_populates="conversation", cascade="all, delete-orphan")


class ChatMessage(Base):
    __tablename__ = "chat_messages"

    id: Mapped[int] = mapped_column(primary_key=True)
    conversation_id: Mapped[int] = mapped_column(ForeignKey("chat_conversations.id"))
    role: Mapped[str] = mapped_column(Text)  # "user" or "ai" or "error"
    content: Mapped[str | None] = mapped_column(Text, nullable=True)
    answer_json: Mapped[str | None] = mapped_column(Text, nullable=True)  # Serialized AnswerResponse JSON
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())

    conversation: Mapped["ChatConversation"] = relationship(back_populates="messages")


class MCQBookmark(Base):
    __tablename__ = "mcq_bookmarks"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    mcq_id: Mapped[int] = mapped_column(ForeignKey("mcqs.id"))
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())

    user: Mapped["User"] = relationship()
    mcq: Mapped["MCQ"] = relationship()


class ConceptBookmark(Base):
    __tablename__ = "concept_bookmarks"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    content: Mapped[str] = mapped_column(Text)
    book_title: Mapped[str | None] = mapped_column(Text, nullable=True)
    page_number: Mapped[int | None] = mapped_column(nullable=True)
    source_context: Mapped[str | None] = mapped_column(Text, nullable=True)  # e.g., "RAG chatbot" or "MCQ Explanation"
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())

    user: Mapped["User"] = relationship()


class Note(Base):
    __tablename__ = "notes"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    title: Mapped[str] = mapped_column(Text, default="Untitled Note")
    content: Mapped[str] = mapped_column(Text)
    book_title: Mapped[str | None] = mapped_column(Text, nullable=True)
    page_number: Mapped[int | None] = mapped_column(nullable=True)
    source_context: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(server_default=func.now(), onupdate=func.now())

    user: Mapped["User"] = relationship()


class Flashcard(Base):
    __tablename__ = "flashcards"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    front: Mapped[str] = mapped_column(Text)
    back: Mapped[str] = mapped_column(Text)
    topic: Mapped[str | None] = mapped_column(Text, default=None)
    book_title: Mapped[str | None] = mapped_column(Text, nullable=True)
    page_number: Mapped[int | None] = mapped_column(nullable=True)
    box: Mapped[int] = mapped_column(default=0)  # spaced-repetition box: 0 = again ... 3 = easy
    last_reviewed: Mapped[datetime | None] = mapped_column(default=None)
    next_due: Mapped[datetime | None] = mapped_column(default=None)
    review_count: Mapped[int] = mapped_column(default=0)
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())

    user: Mapped["User"] = relationship()




class AnswerReport(Base):
    """A user's flag on a chat answer or MCQ, reviewed by admins (migration c9e3a5b7d2f4)."""

    __tablename__ = "answer_reports"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    kind: Mapped[str] = mapped_column(Text)  # 'chat' | 'mcq'
    mcq_id: Mapped[int | None] = mapped_column(ForeignKey("mcqs.id", ondelete="CASCADE"), default=None)
    question: Mapped[str | None] = mapped_column(Text, default=None)
    answer_excerpt: Mapped[str | None] = mapped_column(Text, default=None)
    reason: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(Text, server_default="open")
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())
    resolved_at: Mapped[datetime | None] = mapped_column(default=None)

    user: Mapped["User"] = relationship()

    __table_args__ = (
        CheckConstraint("kind IN ('chat', 'mcq')"),
        CheckConstraint("status IN ('open', 'resolved', 'dismissed')"),
    )


class ConceptCard(Base):
    """A tested concept explained from the textbooks (migration f2b4d6e8a0c1)."""

    __tablename__ = "concept_cards"

    id: Mapped[int] = mapped_column(primary_key=True)
    title: Mapped[str] = mapped_column(Text)
    summary: Mapped[str] = mapped_column(Text)
    quote: Mapped[str | None] = mapped_column(Text, default=None)
    chunk_id: Mapped[int | None] = mapped_column(ForeignKey("chunks.id", ondelete="SET NULL"), default=None)
    book_title: Mapped[str | None] = mapped_column(Text, default=None)
    page_number: Mapped[int | None] = mapped_column(default=None)
    figure_id: Mapped[int | None] = mapped_column(ForeignKey("figures.id", ondelete="SET NULL"), default=None)
    mnemonic: Mapped[str | None] = mapped_column(Text, default=None)
    subject: Mapped[str | None] = mapped_column(Text, default=None)
    source: Mapped[str] = mapped_column(Text, server_default="textbook")      # textbook | recall_book
    grounding: Mapped[str] = mapped_column(Text, server_default="textbook")   # textbook | ai | recall_book
    visibility: Mapped[str] = mapped_column(Text, server_default="all")       # all | admin (private source)
    embedding: Mapped[list[float] | None] = mapped_column(Vector(1024), default=None, deferred=True)
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())


class ConceptReview(Base):
    """Per-user spaced-repetition state for one concept."""

    __tablename__ = "concept_reviews"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    concept_id: Mapped[int] = mapped_column(ForeignKey("concept_cards.id", ondelete="CASCADE"))
    box: Mapped[int] = mapped_column(server_default="0")
    next_due: Mapped[datetime] = mapped_column(server_default=func.now())
    last_result: Mapped[str | None] = mapped_column(Text, default=None)
    lapses: Mapped[int] = mapped_column(server_default="0")
    reviews: Mapped[int] = mapped_column(server_default="0")
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(server_default=func.now(), onupdate=func.now())

    concept: Mapped["ConceptCard"] = relationship()


class AnswerEvent(Base):
    """Every answered MCQ with the student's confidence."""

    __tablename__ = "answer_events"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    mcq_id: Mapped[int | None] = mapped_column(ForeignKey("mcqs.id", ondelete="SET NULL"), default=None)
    concept_id: Mapped[int | None] = mapped_column(ForeignKey("concept_cards.id", ondelete="SET NULL"), default=None)
    selected_option: Mapped[str | None] = mapped_column(Text, default=None)
    is_correct: Mapped[bool]
    confidence: Mapped[str] = mapped_column(Text, server_default="sure")   # sure | unsure | guess
    subject: Mapped[str | None] = mapped_column(Text, default=None)
    source: Mapped[str] = mapped_column(Text, server_default="quiz")       # quiz | dose | retest
    mistake_type: Mapped[str | None] = mapped_column(Text, default=None)   # confusion | misconception | gap
    pair_id: Mapped[int | None] = mapped_column(ForeignKey("confusable_pairs.id", ondelete="SET NULL"), default=None)
    session_ref: Mapped[str | None] = mapped_column(Text, default=None)   # quiz:<id> | dose:<id> | mock:<id> | duel:<id> | practice:<date>
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())


class DailySession(Base):
    """The assembled Daily Dose for one user and day."""

    __tablename__ = "daily_sessions"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    day: Mapped[date]
    items: Mapped[list] = mapped_column(JSONB, default=list)
    completed_at: Mapped[datetime | None] = mapped_column(default=None)
    freeze_used: Mapped[bool] = mapped_column(server_default="false")
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())


class RecallItem(Base):
    """A past-paper recall (question -> published answer) and its textbook verdict (migration a9c1e3f5b7d9)."""

    __tablename__ = "recall_items"

    id: Mapped[int] = mapped_column(primary_key=True)
    source: Mapped[str] = mapped_column(Text)
    page: Mapped[int | None] = mapped_column(default=None)
    chapter: Mapped[str | None] = mapped_column(Text, default=None)
    headline_no: Mapped[int | None] = mapped_column(default=None)
    kind: Mapped[str] = mapped_column(Text, server_default="variant")
    question: Mapped[str] = mapped_column(Text)
    answer: Mapped[str] = mapped_column(Text)
    visibility: Mapped[str] = mapped_column(Text, server_default="admin")
    verdict: Mapped[str | None] = mapped_column(Text, default=None)
    textbook_answer: Mapped[str | None] = mapped_column(Text, default=None)
    evidence: Mapped[list | None] = mapped_column(JSONB, default=None)
    explanation: Mapped[str | None] = mapped_column(Text, default=None)
    concept_id: Mapped[int | None] = mapped_column(ForeignKey("concept_cards.id", ondelete="SET NULL"), default=None)
    mcq_id: Mapped[int | None] = mapped_column(ForeignKey("mcqs.id", ondelete="SET NULL"), default=None)
    review_status: Mapped[str] = mapped_column(Text, server_default="unreviewed")
    reviewer_note: Mapped[str | None] = mapped_column(Text, default=None)
    refereed_at: Mapped[datetime | None] = mapped_column(default=None)
    embedding: Mapped[list[float] | None] = mapped_column(Vector(1024), default=None, deferred=True)
    times_asked: Mapped[int | None] = mapped_column(default=None)   # headlines: recalls that reword it
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())


class Duel(Base):
    """A shared 10-question challenge (migration b2d4f6a8c0e1)."""

    __tablename__ = "duels"

    id: Mapped[int] = mapped_column(primary_key=True)
    code: Mapped[str] = mapped_column(Text, unique=True)
    creator_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    title: Mapped[str | None] = mapped_column(Text, default=None)
    mcq_ids: Mapped[list] = mapped_column(JSONB)
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())
    expires_at: Mapped[datetime]


class DuelEntry(Base):
    """One player's run of a duel."""

    __tablename__ = "duel_entries"

    id: Mapped[int] = mapped_column(primary_key=True)
    duel_id: Mapped[int] = mapped_column(ForeignKey("duels.id", ondelete="CASCADE"))
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    answers: Mapped[dict] = mapped_column(JSONB)
    score: Mapped[int]
    time_ms: Mapped[int | None] = mapped_column(default=None)
    finished_at: Mapped[datetime] = mapped_column(server_default=func.now())

    user: Mapped["User"] = relationship()


class ConfusablePair(Base):
    """Two look-alike concepts a student mixed up (migration e7a9c1d3f5b6)."""

    __tablename__ = "confusable_pairs"

    id: Mapped[int] = mapped_column(primary_key=True)
    pair_key: Mapped[str] = mapped_column(Text, unique=True)
    term_a: Mapped[str] = mapped_column(Text)
    term_b: Mapped[str] = mapped_column(Text)
    card: Mapped[dict | None] = mapped_column(JSONB, default=None)   # rows, discriminator, quotes
    mcq_ids: Mapped[list] = mapped_column(JSONB, default=list)
    grounding: Mapped[str] = mapped_column(Text, server_default="textbook")
    status: Mapped[str] = mapped_column(Text, server_default="pending")
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())


class StudySession(Base):
    """A non-Daily-Dose study session, e.g. the final sprint (migration e7a9c1d3f5b6)."""

    __tablename__ = "study_sessions"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    kind: Mapped[str] = mapped_column(Text)
    day: Mapped[date]
    items: Mapped[list] = mapped_column(JSONB, default=list)
    completed_at: Mapped[datetime | None] = mapped_column(default=None)
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())


class WeeklyMock(Base):
    """One fixed CPSP-format paper per ISO week (migration e7a9c1d3f5b6)."""

    __tablename__ = "weekly_mocks"

    id: Mapped[int] = mapped_column(primary_key=True)
    week_start: Mapped[date]
    part: Mapped[str] = mapped_column(Text, server_default="p1")     # p1 | p2
    track: Mapped[str] = mapped_column(Text, server_default="")      # Paper 2 faculty; '' = mixed
    title: Mapped[str] = mapped_column(Text)
    mcq_ids: Mapped[list] = mapped_column(JSONB)
    duration_min: Mapped[int] = mapped_column(server_default="120")
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())


class WeeklyMockEntry(Base):
    """One student's sitting of a weekly mock."""

    __tablename__ = "weekly_mock_entries"

    id: Mapped[int] = mapped_column(primary_key=True)
    mock_id: Mapped[int] = mapped_column(ForeignKey("weekly_mocks.id", ondelete="CASCADE"))
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    started_at: Mapped[datetime] = mapped_column(server_default=func.now())
    submitted_at: Mapped[datetime | None] = mapped_column(default=None)
    answers: Mapped[dict] = mapped_column(JSONB, default=dict)
    score: Mapped[int | None] = mapped_column(default=None)
    total: Mapped[int | None] = mapped_column(default=None)
    overtime: Mapped[bool] = mapped_column(server_default="false")


class PastPaper(Base):
    """One exam year of an imported past-paper archive (migration b5d7f9a1c3e5)."""

    __tablename__ = "past_papers"

    id: Mapped[int] = mapped_column(primary_key=True)
    code: Mapped[str] = mapped_column(Text, unique=True)
    exam: Mapped[str] = mapped_column(Text)        # e.g. 'FCPS Part 1'
    title: Mapped[str] = mapped_column(Text)       # e.g. 'FCPS Part 1 - 2024'
    year: Mapped[int | None] = mapped_column(default=None)
    source: Mapped[str] = mapped_column(Text)
    access: Mapped[str] = mapped_column(Text, server_default="restricted")
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())


class PastPaperQuestion(Base):
    __tablename__ = "past_paper_questions"

    paper_id: Mapped[int] = mapped_column(ForeignKey("past_papers.id", ondelete="CASCADE"), primary_key=True)
    mcq_id: Mapped[int] = mapped_column(ForeignKey("mcqs.id", ondelete="CASCADE"), primary_key=True)
    position: Mapped[int] = mapped_column(server_default="0")


class MCQTag(Base):
    """Every subject / topic / specialty label of a question."""

    __tablename__ = "mcq_tags"

    mcq_id: Mapped[int] = mapped_column(ForeignKey("mcqs.id", ondelete="CASCADE"), primary_key=True)
    axis: Mapped[str] = mapped_column(Text, primary_key=True)
    label: Mapped[str] = mapped_column(Text, primary_key=True)


class MCQMedia(Base):
    """An image belonging to a question (or its explanation), stored in the DB."""

    __tablename__ = "mcq_media"

    id: Mapped[int] = mapped_column(primary_key=True)
    mcq_id: Mapped[int] = mapped_column(ForeignKey("mcqs.id", ondelete="CASCADE"))
    role: Mapped[str] = mapped_column(Text, server_default="question")   # question | explanation
    mime: Mapped[str] = mapped_column(Text)
    data: Mapped[bytes] = mapped_column(LargeBinary, deferred=True)
    origin: Mapped[str | None] = mapped_column(Text, default=None)
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())


class TopicSummary(Base):
    """A cached one-page revision sheet: a Rapid Review topic scope (exam + tags, or a bank
    category) or a book scope (books + chapter + topic, migration b6e2f8a0c4d7)."""

    __tablename__ = "topic_summaries"

    id: Mapped[int] = mapped_column(primary_key=True)
    scope_key: Mapped[str] = mapped_column(Text, unique=True)
    label: Mapped[str] = mapped_column(Text)
    markdown: Mapped[str] = mapped_column(Text)
    citations: Mapped[list] = mapped_column(JSONB, default=list)
    figures: Mapped[list] = mapped_column(JSONB, default=list, server_default="[]")
    coverage: Mapped[dict] = mapped_column(JSONB, default=dict, server_default="{}")  # book scope only: how much was read
    key_count: Mapped[int] = mapped_column(server_default="0")   # past-paper keys, or book-scope gaps
    access: Mapped[str] = mapped_column(Text, server_default="open")
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())


class SavedSheet(Base):
    """A revision sheet this user has written, so Study Corner can reopen it.

    The cached content itself lives in topic_summaries (shared, keyed by scope); this
    row is only the per-user reminder with the scope needed to reopen it. Deleting it
    here never deletes the cached sheet.
    """

    __tablename__ = "saved_sheets"
    __table_args__ = (UniqueConstraint("user_id", "scope_key", name="uq_saved_sheets_user_scope"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    scope_key: Mapped[str] = mapped_column(Text)
    label: Mapped[str] = mapped_column(Text)
    book_ids: Mapped[list] = mapped_column(JSONB, default=list)
    chapter: Mapped[str | None] = mapped_column(Text, default=None)
    topic: Mapped[str | None] = mapped_column(Text, default=None)
    length: Mapped[str] = mapped_column(Text, default="quick")
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())

    user: Mapped["User"] = relationship()
