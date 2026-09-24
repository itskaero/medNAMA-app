"""SQLAlchemy models matching schema.sql."""

from datetime import datetime

from pgvector.sqlalchemy import Vector
from sqlalchemy import CheckConstraint, ForeignKey, LargeBinary, Text, func
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
