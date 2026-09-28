"""Shared pieces of the API: the DB session dependency, request models used by several routers, and the
background ingest worker. Moved out of app/main.py when it was split into app/routers/."""

import logging
import os
from pydantic import BaseModel
from app.database import SessionLocal
from app.ingestion import ingest_book
from app.models import MCQ

logger = logging.getLogger("app.main")   # same logger name as before the split

# Dependency to get db session
def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

# Pydantic schemas
class QueryRequest(BaseModel):
    query: str
    confidence_threshold: float = 0.55

class ChatQueryRequest(BaseModel):
    query: str
    conversation_id: int | None = None
    confidence_threshold: float = 0.55
    book_id: int | None = None
    chapter: str | None = None
    level: str | None = None  # 'undergraduate' | 'fcps1' | 'fcps2'

class ConceptBookmarkCreate(BaseModel):
    content: str
    book_title: str | None = None
    page_number: int | None = None
    source_context: str | None = None

class UserRegister(BaseModel):
    username: str
    password: str
    role: str = "student"

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
