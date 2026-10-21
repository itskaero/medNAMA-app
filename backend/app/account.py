"""Account data export and deletion (revision.md §10: "data export and deletion").

Most per-user tables already carry ``ON DELETE CASCADE`` (answer_events, concept_reviews,
daily_sessions, duel_entries, duels, study_sessions, weekly_mock_entries, saved_sheets,
answer_reports). The rest reference ``users.id`` with no ``ON DELETE`` action, so deleting a
user without clearing them first raises a foreign-key violation. ``delete_user_data`` clears
every table explicitly, children before parents, so it works regardless of the FK action and
leaves no orphaned rows behind.

Nothing here touches shared content: MCQs, concept cards, past papers, books and chunks are
community/library data and are never removed when an account is deleted.
"""

from __future__ import annotations

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.models import (
    AnswerEvent,
    AnswerReport,
    ChatConversation,
    ChatMessage,
    ConceptBookmark,
    ConceptReview,
    DailySession,
    Duel,
    DuelEntry,
    Flashcard,
    InviteCode,
    MCQBookmark,
    Note,
    QuizAttempt,
    SavedSheet,
    StudySession,
    User,
    WeeklyMockEntry,
)

# Tables holding a user's own rows, keyed by user_id. Cleared explicitly (not left to
# ON DELETE) so the delete is correct regardless of each FK's action. `duels` is the one
# exception: a duel is owned by its creator_id, not a user_id column, so it is handled
# separately below.
_USER_ID_TABLES = (
    "quiz_attempts",
    "chat_conversations",
    "mcq_bookmarks",
    "concept_bookmarks",
    "notes",
    "flashcards",
    "answer_events",
    "answer_reports",
    "concept_reviews",
    "daily_sessions",
    "duel_entries",
    "study_sessions",
    "weekly_mock_entries",
    "saved_sheets",
    "station_challenges",   # before station_attempts (their scores go with the attempt, ON DELETE CASCADE)
    "station_attempts",
)


def delete_user_data(db: Session, user_id: int) -> None:
    """Delete every row belonging to a user, then the user, in one committed transaction.

    Children are cleared before the parents that reference them (attempt_answers and
    chat_messages have no ON DELETE action on their parent FKs), and invite codes the user
    created are kept with the creator link dropped rather than deleting shared sign-up codes.
    """
    # Children of quiz_attempts / chat_conversations (RESTRICT on the parent FK).
    db.execute(
        text("DELETE FROM attempt_answers WHERE quiz_attempt_id IN "
             "(SELECT id FROM quiz_attempts WHERE user_id = :u)"),
        {"u": user_id},
    )
    db.execute(
        text("DELETE FROM chat_messages WHERE conversation_id IN "
             "(SELECT id FROM chat_conversations WHERE user_id = :u)"),
        {"u": user_id},
    )
    for table in _USER_ID_TABLES:
        db.execute(text(f"DELETE FROM {table} WHERE user_id = :u"), {"u": user_id})
    # A duel is owned by its creator (duels.creator_id), with no user_id column.
    db.execute(text("DELETE FROM duels WHERE creator_id = :u"), {"u": user_id})
    # Keep the invite code; only drop the link to the account that created it.
    db.execute(text("UPDATE invite_codes SET created_by = NULL WHERE created_by = :u"), {"u": user_id})
    user = db.get(User, user_id)
    if user is not None:
        db.delete(user)
    db.commit()


def _row_to_dict(obj) -> dict:
    """A plain dict of one row's columns (JSON-encoded later with default=str)."""
    return {c.name: getattr(obj, c.name) for c in obj.__table__.columns}


def export_user_data(db: Session, user: User) -> dict:
    """Every row the platform holds about one account, as a JSON-serialisable dict.

    Used by the self-service export (§10 "data export"). Shared library content is included
    only where the user has a personal row on it (bookmarks, reviews, saved sheets), never the
    content itself.
    """
    def rows(model, *filters) -> list[dict]:
        query = db.query(model)
        for f in filters:
            query = query.filter(f)
        return [_row_to_dict(r) for r in query.all()]

    attempts = [_row_to_dict(a) | {"answers": [_row_to_dict(x) for x in a.answers]}
                for a in db.query(QuizAttempt).filter(QuizAttempt.user_id == user.id).all()]
    conversations = [_row_to_dict(c) | {"messages": [_row_to_dict(m) for m in c.messages]}
                     for c in db.query(ChatConversation).filter(ChatConversation.user_id == user.id).all()]

    return {
        "profile": _row_to_dict(user),
        "answer_events": rows(AnswerEvent, AnswerEvent.user_id == user.id),
        "quiz_attempts": attempts,
        "chat_conversations": conversations,
        "notes": rows(Note, Note.user_id == user.id),
        "flashcards": rows(Flashcard, Flashcard.user_id == user.id),
        "concept_bookmarks": rows(ConceptBookmark, ConceptBookmark.user_id == user.id),
        "mcq_bookmarks": rows(MCQBookmark, MCQBookmark.user_id == user.id),
        "concept_reviews": rows(ConceptReview, ConceptReview.user_id == user.id),
        "answer_reports": rows(AnswerReport, AnswerReport.user_id == user.id),
        "daily_sessions": rows(DailySession, DailySession.user_id == user.id),
        "study_sessions": rows(StudySession, StudySession.user_id == user.id),
        "saved_sheets": rows(SavedSheet, SavedSheet.user_id == user.id),
        "duels_created": rows(Duel, Duel.creator_id == user.id),
        "duel_entries": rows(DuelEntry, DuelEntry.user_id == user.id),
        "weekly_mock_entries": rows(WeeklyMockEntry, WeeklyMockEntry.user_id == user.id),
        **_station_rows(db, user.id, rows),
    }


def _station_rows(db: Session, user_id: int, rows) -> dict:
    """TOACS attempts with their criterion scores, and the learner's score challenges."""
    from app.station_models import StationAttempt, StationChallenge, StationScore

    attempts = rows(StationAttempt, StationAttempt.user_id == user_id)
    ids = [a["id"] for a in attempts]
    scores = rows(StationScore, StationScore.attempt_id.in_(ids)) if ids else []
    for a in attempts:
        a["scores"] = [s for s in scores if s["attempt_id"] == a["id"]]
    return {"station_attempts": attempts,
            "station_challenges": rows(StationChallenge, StationChallenge.user_id == user_id)}
