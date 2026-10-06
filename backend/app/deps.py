"""Shared pieces of the API: the DB session dependency, request models used by several routers, and the
background ingest worker. Moved out of app/main.py when it was split into app/routers/."""

import logging
import os
import re
from pydantic import BaseModel, field_validator
from app.database import get_db  # noqa: F401  (the one session dependency; see app/database.py)
from app.ingestion import ingest_book
from app.models import MCQ

logger = logging.getLogger("app.main")   # same logger name as before the split

# Pydantic schemas
class QueryRequest(BaseModel):
    query: str
    confidence_threshold: float = 0.55

class ChatQueryRequest(BaseModel):
    query: str
    conversation_id: int | None = None
    confidence_threshold: float = 0.55
    book_id: int | None = None
    book_ids: list[int] | None = None  # source scope: a hard boundary, nothing else is retrieved
    chapter: str | None = None
    # Exam programme (app/exam_levels.py): MBBS | FCPS-I | FCPS-II | PGET | MD | MS | USMLE.
    # The old 'undergraduate' | 'fcps1' | 'fcps2' values still resolve; unknown = general prep.
    level: str | None = None
    mode: str | None = None   # 'tutor': Socratic, one question at a time (app/generation.py TUTOR_RULES)

class ConceptBookmarkCreate(BaseModel):
    content: str
    book_title: str | None = None
    page_number: int | None = None
    source_context: str | None = None

USERNAME_RE = re.compile(r"^[A-Za-z0-9._-]{3,30}$")
USERNAME_RULE = ("A username must be 3-30 characters and use only letters, numbers, "
                 "dot, underscore or hyphen - no spaces.")


class UserRegister(BaseModel):
    username: str
    password: str
    role: str = "student"
    invite_code: str | None = None   # needed when REGISTRATION_MODE=invite

    @field_validator("username")
    @classmethod
    def _clean_username(cls, value: str) -> str:
        """One place to decide what a username may be; /api/auth/register is the only
        route that creates accounts, so validating here covers every caller.

        Spaces were accepted before ("john doe"), and because the duplicate check read the
        raw value while the insert stored a stripped one, " ali " alongside an existing
        "ali" slipped past the friendly check and hit the UNIQUE constraint as a 500.
        Returning the trimmed value means the check and the insert now see the same string.
        """
        cleaned = (value or "").strip()
        if not USERNAME_RE.fullmatch(cleaned):
            raise ValueError(USERNAME_RULE)
        return cleaned

# Ingestion background task worker
def bg_ingest_worker(temp_pdf_path: str, filename: str, book_title: str):
    """Executes the ingestion pipeline in a background task."""
    try:
        ingest_book(temp_pdf_path, title=book_title)
    except Exception as e:
        print(f"Background ingestion of {filename} failed: {e}")
    finally:
        # Clean up temp file
        if os.path.exists(temp_pdf_path):
            try:
                os.remove(temp_pdf_path)
            except Exception as e:
                print(f"Failed to remove temp file {temp_pdf_path}: {e}")

def _mcq_payload(m: MCQ) -> dict:
    from sqlalchemy.orm import object_session

    from app.past_papers import question_media

    sess = object_session(m)
    media = question_media(sess, [m.id]).get(m.id, []) if sess is not None else []
    return {
        "media": media,
        "id": m.id,
        "question_text": m.question_text,
        "options": m.options,
        "correct_option": m.correct_option,
        "explanation_markdown": m.explanation_markdown,
        "sub_category": m.sub_category,
        "main_category": m.main_category,
        "figure_id": m.figure_id,
        "concept_id": m.concept_id,
        "grounding": m.grounding,
    }

class MockAnswersRequest(BaseModel):
    answers: dict = {}
