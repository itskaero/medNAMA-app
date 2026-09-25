"""FastAPI application entry point.

Defines HTTP API endpoints for authentication (login, registration, profiles),
book management, PDF ingestion, hybrid RAG query answering, and figure rendering.
"""

import logging
import os
import shutil
import tempfile
import json
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from fastapi import BackgroundTasks, Depends, FastAPI, File, HTTPException, UploadFile, status, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import Response, StreamingResponse
from fastapi.security import OAuth2PasswordRequestForm
from pydantic import BaseModel
from sqlalchemy import func
from sqlalchemy.orm import Session, joinedload

from app.config import settings
from app.database import SessionLocal, engine
from app.generation import generate_answer, generate_mcq_explanation
from app.ingestion import ingest_book
from app.models import (
    Base, Book, Chunk, Figure, User, MCQ, QuizAttempt, AttemptAnswer,
    ChatConversation, ChatMessage, MCQBookmark, ConceptBookmark, Note, Flashcard, AnswerReport
)
from app.auth import (
    hash_password,
    verify_password,
    create_access_token,
    get_current_user,
    require_admin,
    require_student_or_admin,
    rate_limiter,
)

# App loggers (app.retrieval, app.generation, ...) had no handler, so their INFO
# lines never reached `docker logs`. uvicorn's own loggers are unaffected.
logging.basicConfig(
    level=getattr(logging, settings.log_level.upper(), logging.INFO),
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
for _noisy in ("httpx", "httpcore", "openai", "sentence_transformers", "huggingface_hub"):
    logging.getLogger(_noisy).setLevel(logging.WARNING)
logger = logging.getLogger(__name__)

# Initialize FastAPI app
app = FastAPI(title="medNAMA Core API", version="1.0.0")

# Custom HTTP middleware enforcing security headers (Proposal 11)
@app.middleware("http")
async def add_security_headers(request: Request, call_next):
    response = await call_next(request)
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-XSS-Protection"] = "1; mode=block"
    response.headers["Content-Security-Policy"] = (
        "default-src 'self'; "
        "img-src 'self' data: http: https:; "
        "script-src 'self' 'unsafe-inline' 'unsafe-eval'; "
        "style-src 'self' 'unsafe-inline' https://fonts.googleapis.com; "
        "font-src 'self' https://fonts.gstatic.com;"
    )
    response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
    return response


# Enable CORS LAST so CORSMiddleware is the outermost middleware handling preflights & headers for all responses
raw_origins = [origin.strip().rstrip("/") for origin in settings.allowed_origins.split(",") if origin.strip()]
trusted_domains = [
    "https://med-nama.vercel.app",
    "http://localhost:3000",
    "http://127.0.0.1:3000",
]
for d in trusted_domains:
    if d not in raw_origins:
        raw_origins.append(d)

app.add_middleware(
    CORSMiddleware,
    allow_origins=raw_origins,
    allow_origin_regex=r"https://.*\.vercel\.app",
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.exception_handler(Exception)
async def global_exception_handler(request: Request, exc: Exception):
    import logging
    logging.getLogger("uvicorn.error").error(f"Global unhandled exception: {exc}", exc_info=True)
    from fastapi.responses import JSONResponse
    return JSONResponse(
        status_code=500,
        content={"detail": f"Internal Server Error: {str(exc)}"},
    )


# Warm up models on application startup to avoid first-query cold-start delay
@app.on_event("startup")
def warmup_models():
    """Build any missing database tables and warm up models on startup."""
    print("INITIALIZING DATABASE TABLES...")
    Base.metadata.create_all(bind=engine)
    from app.ingestion import get_embedding_model
    from app.retrieval import get_reranker_model

    print("\n" + "="*60)
    print("WARMING UP QUANTIZED TEXT EMBEDDING MODEL...")
    _ = get_embedding_model()
    print("WARMING UP QUANTIZED CROSS-ENCODER RERANKER MODEL...")
    _ = get_reranker_model()
    from app.retrieval import get_second_stage_reranker
    print("WARMING UP SECOND-STAGE (BIOMEDICAL) RERANKER...")
    _ = get_second_stage_reranker()
    print("Warmup complete. All models preloaded and INT8 quantized in RAM.")
    print("="*60 + "\n")


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


# ======================== AUTHENTICATION ROUTING ========================

@app.post(
    "/api/auth/register",
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(rate_limiter(limit=5, window=60))],
)
def register_user(user_in: UserRegister, db: Session = Depends(get_db)):
    """Registers a new user (defaults to student role)."""
    # Check duplicate
    existing = db.query(User).filter(User.username == user_in.username).first()
    if existing:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Username is already taken.")

    if user_in.role not in ["student", "admin"]:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid role specification.")

    hashed_pw = hash_password(user_in.password)
    user = User(username=user_in.username, password_hash=hashed_pw, role="student")
    db.add(user)
    db.commit()

    return {"message": "Registration successful.", "username": user.username, "role": user.role}


@app.post(
    "/api/auth/login",
    dependencies=[Depends(rate_limiter(limit=10, window=60))],
)
def login_user(
    response: Response,
    form_data: OAuth2PasswordRequestForm = Depends(),
    db: Session = Depends(get_db),
):
    """Authenticates credentials, sets HttpOnly cookie, and returns credentials."""
    user = db.query(User).filter(User.username == form_data.username).first()
    if not user or not verify_password(form_data.password, user.password_hash):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Incorrect username or password."
        )

    token = create_access_token(data={"sub": user.username})

    # Set access token inside secure HttpOnly cookie (Proposal 1)
    response.set_cookie(
        key="access_token",
        value=token,
        httponly=True,
        secure=False,  # In production, set to True when using HTTPS
        samesite="lax",
        max_age=3600 * 24, # 1 day expiration
    )

    return {
        "access_token": token,
        "token_type": "bearer",
        "role": user.role,
        "username": user.username,
    }


@app.post("/api/auth/logout")
def logout_user(response: Response):
    """Logs out current session by deleting the HttpOnly cookie."""
    response.delete_cookie(key="access_token")
    return {"message": "Logged out successfully."}


@app.get("/api/auth/me")
def get_user_profile(current_user: User = Depends(get_current_user)):
    """Returns profile information for the authenticated user."""
    return {"username": current_user.username, "role": current_user.role}


# ======================== TEXTBOOK MANAGEMENT ========================

@app.get("/api/books")
def list_books(
    db: Session = Depends(get_db),
    current_user: User = Depends(require_student_or_admin)
):
    """List all textbooks stored in the database."""
    books = db.query(Book).order_by(Book.id.desc()).all()
    return [
        {
            "id": b.id,
            "title": b.title,
            "filename": b.filename,
            "status": b.status,
            "total_pages": b.total_pages,
            "error_message": b.error_message,
            "created_at": b.created_at,
        }
        for b in books
    ]


@app.delete("/api/books/{book_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_book(
    book_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_admin)
):
    """Delete a book and all associated chunks and figures from the database (Admin only)."""
    book = db.query(Book).filter(Book.id == book_id).first()
    if not book:
        raise HTTPException(status_code=404, detail="Book not found")

    db.delete(book)
    db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@app.get("/api/books/{book_id}/chapters")
def list_book_chapters(
    book_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_student_or_admin)
):
    """Returns distinct chapter headings for a book (for scoped chat retrieval).

    Filters out junk/watermark slugs (e.g. 'mebooksfree.com', blank, single-char)
    so the chapter dropdown stays clean.
    """
    book = db.query(Book).filter(Book.id == book_id).first()
    if not book:
        raise HTTPException(status_code=404, detail="Book not found")

    rows = (
        db.query(Chunk.chapter)
        .filter(Chunk.book_id == book_id)
        .filter(Chunk.chapter.isnot(None))
        .distinct()
        .all()
    )
    chapters = []
    for (c,) in rows:
        name = (c or "").strip()
        if not name or len(name) < 2:
            continue
        lower = name.lower()
        if "mebooksfree" in lower or "watermark" in lower or "publisher" in lower:
            continue
        if name not in chapters:
            chapters.append(name)
    chapters.sort(key=str.lower)
    return {"book_id": book_id, "book_title": book.title, "chapters": chapters}


@app.post(
    "/api/ingest",
    status_code=status.HTTP_202_ACCEPTED,
    dependencies=[Depends(rate_limiter(limit=5, window=60))],
)
def upload_and_ingest_book(
    background_tasks: BackgroundTasks,
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
    current_user: User = Depends(require_admin)
):
    """Uploads a PDF and spawns background ingestion (Admin only)."""
    # 1. Enforce content MIME type and extension limits (Proposal 10)
    if not file.filename.endswith(".pdf") or file.content_type != "application/pdf":
        raise HTTPException(status_code=400, detail="Only PDF files are supported.")

    max_bytes = settings.max_upload_size_mb * 1024 * 1024
    content_size = 0
    temp_dir = tempfile.gettempdir()
    temp_file_path = os.path.join(temp_dir, f"medrag_upload_{os.urandom(8).hex()}.pdf")

    try:
        with open(temp_file_path, "wb") as buffer:
            while chunk := file.file.read(1024 * 1024):
                content_size += len(chunk)
                if content_size > max_bytes:
                    raise HTTPException(
                        status_code=400,
                        detail=f"Upload exceeds maximum size limit of {settings.max_upload_size_mb}MB.",
                    )
                buffer.write(chunk)
    except HTTPException:
        if os.path.exists(temp_file_path):
            os.remove(temp_file_path)
        raise
    except Exception as e:
        if os.path.exists(temp_file_path):
            os.remove(temp_file_path)
        raise HTTPException(status_code=500, detail=f"File save error: {e}")

    # 2. Open PDF with pypdf to check if encrypted or corrupted (Proposal 10)
    from pypdf import PdfReader
    try:
        reader = PdfReader(temp_file_path)
        if reader.is_encrypted:
            raise HTTPException(status_code=400, detail="Encrypted/password-protected PDFs are not supported.")
        _ = len(reader.pages) # simple extraction check
    except HTTPException:
        if os.path.exists(temp_file_path):
            os.remove(temp_file_path)
        raise
    except Exception as e:
        if os.path.exists(temp_file_path):
            os.remove(temp_file_path)
        raise HTTPException(status_code=400, detail=f"Failed to read PDF. The file might be corrupted: {e}")

    existing = db.query(Book).filter(Book.filename == file.filename).first()
    if existing:
        if os.path.exists(temp_file_path):
            os.remove(temp_file_path)
        raise HTTPException(status_code=400, detail="A book with this filename has already been uploaded.")

    title = Path(file.filename).stem.replace("-", " ").replace("_", " ").title()

    background_tasks.add_task(
        bg_ingest_worker,
        temp_pdf_path=temp_file_path,
        filename=file.filename,
        book_title=title,
    )

    return {"message": f"Book '{title}' uploaded. Ingestion started."}


# ======================== QUERYING & DIAGRAMS ========================

@app.post(
    "/api/query",
    dependencies=[Depends(rate_limiter(limit=15, window=60))],
)
def query_rag(
    request: QueryRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_student_or_admin)
):
    """Processes RAG question answering using hybrid search and DeepSeek."""
    if not request.query.strip():
        raise HTTPException(status_code=400, detail="Query cannot be empty.")

    answer_json = generate_answer(
        session=db, query=request.query, confidence_threshold=request.confidence_threshold
    )
    return answer_json


@app.get("/api/figures/{figure_id}")
def get_figure_image(
    figure_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_student_or_admin)
):
    """Serves raw diagram image bytes directly from database with correct content headers."""
    figure = db.query(Figure).filter(Figure.id == figure_id).first()
    if not figure:
        raise HTTPException(status_code=404, detail="Figure not found.")

    return Response(content=figure.image_data, media_type=figure.mime_type)


@app.get("/api/dashboard/stats")
def get_dashboard_stats(
    db: Session = Depends(get_db),
    current_user: User = Depends(require_student_or_admin)
):
    """Returns general statistics and topic lists for the student/admin dashboard."""
    from sqlalchemy import func
    
    total_books = db.query(Book).count()
    total_mcqs = db.query(MCQ).count()
    total_quizzes_taken = db.query(QuizAttempt).filter(QuizAttempt.user_id == current_user.id).count()
    
    attempts = db.query(QuizAttempt).filter(
        QuizAttempt.user_id == current_user.id,
        QuizAttempt.score.isnot(None)
    ).all()
    
    avg_score = 0.0
    if attempts:
        avg_score = sum(a.score for a in attempts) / len(attempts)
        
    # Get distinct main_category, sub_category and their counts
    categories_query = db.query(
        MCQ.main_category,
        MCQ.sub_category,
        func.count(MCQ.id)
    ).group_by(MCQ.main_category, MCQ.sub_category).all()
    
    categories_map = {}
    for main, sub, count in categories_query:
        if not main or not sub:
            continue
        if main not in categories_map:
            categories_map[main] = []
        categories_map[main].append({"name": sub, "count": count})
        
    for main in categories_map:
        categories_map[main] = sorted(categories_map[main], key=lambda x: x["name"])
        
    categories_list = [
        {"main_category": main, "sub_categories": sub_list}
        for main, sub_list in sorted(categories_map.items())
    ]

    recent_attempts = []
    db_attempts = db.query(QuizAttempt).filter(
        QuizAttempt.user_id == current_user.id,
        QuizAttempt.score.isnot(None)
    ).order_by(QuizAttempt.completed_at.desc()).limit(5).all()

    for att in db_attempts:
        first_ans = db.query(AttemptAnswer).filter(AttemptAnswer.quiz_attempt_id == att.id).first()
        category_name = "General Practice"
        if first_ans:
            mcq = db.query(MCQ).filter(MCQ.id == first_ans.mcq_id).first()
            if mcq:
                category_name = mcq.sub_category or mcq.main_category or "General Practice"
        
        recent_attempts.append({
            "id": att.id,
            "started_at": att.started_at.isoformat() if att.started_at else None,
            "completed_at": att.completed_at.isoformat() if att.completed_at else None,
            "score": att.score,
            "total_questions": att.total_questions,
            "category": category_name
        })

    return {
        "total_books": total_books,
        "total_mcqs": total_mcqs,
        "total_quizzes_taken": total_quizzes_taken,
        "average_score": round(avg_score, 1),
        "categories": categories_list,
        "recent_attempts": recent_attempts
    }


@app.post("/api/mcqs/{mcq_id}/explain")
def explain_mcq_endpoint(
    mcq_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_student_or_admin)
):
    """On-demand RAG-grounded explanation generation and caching for a specific MCQ."""
    mcq = db.query(MCQ).filter(MCQ.id == mcq_id).first()
    if not mcq:
        raise HTTPException(status_code=404, detail="MCQ not found.")

    # Return cached explanation if present
    if mcq.explanation_markdown and mcq.explanation_markdown.strip() and mcq.status == "ready":
        return {
            "answer_markdown": mcq.explanation_markdown,
            "citations": mcq.explanation_citations or [],
            "figures": mcq.explanation_figures or []
        }

    # Generate and cache explanation
    explanation_data = generate_mcq_explanation(db, mcq)
    
    # Save cache back to DB
    mcq.explanation_markdown = explanation_data.get("answer_markdown")
    mcq.explanation_citations = explanation_data.get("citations")
    mcq.explanation_figures = explanation_data.get("figures")
    mcq.status = "ready"
    db.commit()

    return explanation_data


# ─── AI Quiz Generation System ───────────────────────────────────

class GenerateAiQuizRequest(BaseModel):
    prompt: str
    book_id: int | None = None
    page_number: int | None = None
    count: int | None = None
    difficulty: int | None = None  # 1 (easy) - 5 (hard); falls back to AI_MCQ_DIFFICULTY env
    exam_profile: str | None = None  # 'fcps' (A-E, default) | 'usmle' (A-E vignette) | 'quick' (A-D recall)
    request_id: str | None = None  # client idempotency key; a retry returns the same set
    chapter: str | None = None     # restrict to one chapter of book_id (Study-a-chapter)


def _resolve_quiz_params(req: GenerateAiQuizRequest) -> dict:
    """Validate a quiz request and resolve page / count / difficulty from the prompt and env."""
    import re

    prompt_text = req.prompt.strip()
    if not prompt_text:
        raise HTTPException(status_code=400, detail="Prompt cannot be empty.")

    page_num = req.page_number
    if page_num is None:
        page_match = re.search(r"page\s*#?\s*(\d+)", prompt_text, re.IGNORECASE)
        if page_match:
            page_num = int(page_match.group(1))

    # Multiples of 5 only, max 20
    ALLOWED_COUNTS = (5, 10, 15, 20)
    mcq_count = req.count or 5
    count_match = re.search(r"(\d+)\s*(?:questions?|mcqs?|items?)", prompt_text, re.IGNORECASE)
    if count_match:
        mcq_count = int(count_match.group(1))
    if mcq_count not in ALLOWED_COUNTS:
        raise HTTPException(
            status_code=400,
            detail="count must be a multiple of 5 between 5 and 20 (5, 10, 15, or 20).",
        )

    # Difficulty: explicit request > AI_MCQ_DIFFICULTY env override > unspecified
    difficulty = req.difficulty
    if difficulty is None:
        env_diff = os.environ.get("AI_MCQ_DIFFICULTY", "").strip()
        if env_diff.isdigit():
            difficulty = int(env_diff)
    if difficulty is not None and not (1 <= difficulty <= 5):
        raise HTTPException(status_code=400, detail="difficulty must be an integer between 1 and 5.")

    from app.llm import llm_configured

    if not llm_configured("fast"):
        raise HTTPException(status_code=400, detail="DEEPSEEK_API_KEY is not configured on the server.")

    return {
        "prompt_text": prompt_text,
        "book_id": req.book_id,
        "page_num": page_num,
        "mcq_count": mcq_count,
        "difficulty": difficulty,
        "exam_profile": req.exam_profile,
        "request_id": req.request_id,
        "chapter": (req.chapter or None) if req.book_id else None,
    }


@app.post("/api/chat/generate-ai-quiz")
def generate_ai_quiz(
    req: GenerateAiQuizRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_student_or_admin),
):
    """Generates MCQs synchronously (kept for API clients; the UI uses the job endpoints)."""
    from app.quiz_generation import QuizGenerationError, generate_quiz_set

    params = _resolve_quiz_params(req)
    try:
        return generate_quiz_set(db, **params)
    except QuizGenerationError as e:
        raise HTTPException(status_code=e.status_code, detail=e.detail)


@app.post("/api/chat/generate-ai-quiz/jobs", status_code=status.HTTP_202_ACCEPTED)
def start_ai_quiz_job(
    req: GenerateAiQuizRequest,
    current_user: User = Depends(require_student_or_admin),
):
    """Start MCQ generation in the background and return a job id to poll.

    The request returns immediately, so no proxy or browser timeout can cut it
    off however long generation takes. Idempotent on request_id.
    """
    from app.quiz_generation import start_quiz_job

    params = _resolve_quiz_params(req)
    return start_quiz_job(params)


@app.get("/api/chat/generate-ai-quiz/jobs/{job_id}")
def get_ai_quiz_job(
    job_id: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_student_or_admin),
):
    """Poll a background MCQ job: {status: running|done|failed, stage?, result?, detail?}."""
    from app.quiz_generation import get_quiz_job

    job = get_quiz_job(db, job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Quiz job not found.")
    return job


@app.get("/api/chat/ai-quizzes/{quiz_set_id}")
def get_ai_quiz_set(
    quiz_set_id: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_student_or_admin),
):
    """Return a saved AI quiz set (used to recover a set whose generate request timed out client-side)."""
    from app.quiz_generation import load_quiz_set, serialize_quiz_set

    mcqs = load_quiz_set(db, quiz_set_id)
    if not mcqs:
        raise HTTPException(status_code=404, detail="Quiz set not found.")
    return serialize_quiz_set(mcqs, quiz_set_id)


@app.get("/api/chat/ai-quizzes")
def get_ai_quizzes_history(
    db: Session = Depends(get_db),
    current_user: User = Depends(require_student_or_admin),
):
    """Returns list of distinct AI-generated quiz sets for user history."""
    results = (
        db.query(
            MCQ.quiz_set_id,
            MCQ.quiz_set_title,
            func.count(MCQ.id).label("question_count"),
            func.max(MCQ.topic).label("topic"),
            func.max(MCQ.difficulty).label("difficulty"),
        )
        .filter(MCQ.quiz_set_id != None)
        .group_by(MCQ.quiz_set_id, MCQ.quiz_set_title)
        .order_by(func.max(MCQ.id).desc())
        .all()
    )

    return [
        {
            "quiz_set_id": r.quiz_set_id,
            "quiz_set_title": r.quiz_set_title or "AI Quiz Set",
            "question_count": r.question_count,
            "topic": r.topic,
            "difficulty": r.difficulty,
        }
        for r in results
    ]


@app.delete("/api/chat/ai-quizzes/{quiz_set_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_ai_quiz_set(
    quiz_set_id: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_student_or_admin),
):
    """Deletes all MCQs in a specific AI quiz set."""
    db.query(MCQ).filter(MCQ.quiz_set_id == quiz_set_id).delete(synchronize_session=False)
    db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


# ─── Practice Quiz Management (Phase 11) ───────────────────────

class StartQuizRequest(BaseModel):
    quiz_set_id: str | None = None
    categories: list[str] | None = None
    sub_categories: list[str] | None = None
    num_questions: int = 10
    exclude_mastered: bool = False
    drill_wrong: bool = False                 # answer-only the user's missed MCQs
    prefer_unseen: bool = True                # questions the user has never attempted come first
    timer_mode: str = "none"                      # "none" | "session" | "per_question"
    timer_value: int | None = None                # minutes or seconds
    feedback_mode: str = "tutor"                  # "tutor" | "board"


class SelectedAnswer(BaseModel):
    mcq_id: int
    selected_option: str
    confidence: str | None = None   # 'sure' | 'unsure' | 'guess' (retention engine)


class SubmitQuizRequest(BaseModel):
    answers: list[SelectedAnswer]


@app.post("/api/quizzes/start")
def start_quiz_endpoint(
    req: StartQuizRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_student_or_admin)
):
    """Generates a randomized practice quiz, creates a QuizAttempt record, and returns the questions."""
    from sqlalchemy import func
    from app.models import AttemptAnswer, QuizAttempt
    
    query = db.query(MCQ)
    
    # Drill mode: only previously-missed questions (user-scoped by construct)
    if req.drill_wrong:
        wrong_ids = (
            db.query(AttemptAnswer.mcq_id)
            .join(QuizAttempt, QuizAttempt.id == AttemptAnswer.quiz_attempt_id)
            .filter(
                QuizAttempt.user_id == current_user.id,
                AttemptAnswer.is_correct.is_(False),
            )
            .distinct()
            .subquery()
        )
        query = query.filter(MCQ.id.in_(wrong_ids))

    # Topic filters
    if req.quiz_set_id:
        query = query.filter(MCQ.quiz_set_id == req.quiz_set_id)
    elif req.sub_categories:
        query = query.filter(MCQ.sub_category.in_(req.sub_categories))
    elif req.categories:
        query = query.filter(MCQ.main_category.in_(req.categories))
        
    # Exclude mastered questions (answered correctly in any past attempt)
    if req.exclude_mastered:
        mastered_subquery = db.query(AttemptAnswer.mcq_id).join(
            QuizAttempt, QuizAttempt.id == AttemptAnswer.quiz_attempt_id
        ).filter(
            QuizAttempt.user_id == current_user.id,
            AttemptAnswer.is_correct == True
        ).subquery()
        query = query.filter(MCQ.id.notin_(mastered_subquery))
        
    # Unseen first: never-attempted questions before ones already answered, so a
    # growing bank keeps producing fresh practice. Random within each group.
    if req.prefer_unseen and not req.drill_wrong:
        from sqlalchemy import case

        seen_subquery = db.query(AttemptAnswer.mcq_id).join(
            QuizAttempt, QuizAttempt.id == AttemptAnswer.quiz_attempt_id
        ).filter(QuizAttempt.user_id == current_user.id).subquery()
        seen_rank = case((MCQ.id.in_(db.query(seen_subquery.c.mcq_id)), 1), else_=0)
        mcqs = query.order_by(seen_rank, func.random()).limit(req.num_questions).all()
    else:
        mcqs = query.order_by(func.random()).limit(req.num_questions).all()
    
    if not mcqs:
        raise HTTPException(
            status_code=400,
            detail="No questions found matching the specified filters."
        )
        
    attempt = QuizAttempt(
        user_id=current_user.id,
        total_questions=len(mcqs),
        timer_mode=req.timer_mode,
        timer_value=req.timer_value,
        feedback_mode=req.feedback_mode
    )
    db.add(attempt)
    db.commit()
    db.refresh(attempt)
    
    # Format questions (excluding deep explanation details initially)
    mcqs_data = []
    for m in mcqs:
        mcqs_data.append({
            "id": m.id,
            "question_text": m.question_text,
            "options": m.options,
            "correct_option": m.correct_option,
            "main_category": m.main_category,
            "sub_category": m.sub_category
        })
        
    return {
        "quiz_attempt_id": attempt.id,
        "mcqs": mcqs_data,
        "timer_mode": attempt.timer_mode,
        "timer_value": attempt.timer_value,
        "feedback_mode": attempt.feedback_mode
    }


@app.post("/api/quizzes/{attempt_id}/submit")
def submit_quiz_endpoint(
    attempt_id: int,
    req: SubmitQuizRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_student_or_admin)
):
    """Scores a completed practice quiz, records individual choices, and saves results to the database."""
    from datetime import datetime
    
    attempt = db.query(QuizAttempt).filter(
        QuizAttempt.id == attempt_id,
        QuizAttempt.user_id == current_user.id
    ).first()
    
    if not attempt:
        raise HTTPException(
            status_code=404,
            detail="Quiz attempt session not found."
        )
        
    if attempt.completed_at is not None:
        raise HTTPException(
            status_code=400,
            detail="This quiz attempt has already been submitted and completed."
        )
        
    # Build a lookup dictionary of MCQs involved in the attempt to minimize DB queries
    mcq_ids = [ans.mcq_id for ans in req.answers]
    mcqs = db.query(MCQ).filter(MCQ.id.in_(mcq_ids)).all()
    mcq_map = {m.id: m for m in mcqs}
    
    correct_count = 0
    attempt_answers = []
    
    for ans in req.answers:
        mcq = mcq_map.get(ans.mcq_id)
        if not mcq:
            raise HTTPException(
                status_code=400,
                detail=f"Question with ID {ans.mcq_id} is invalid or not found."
            )
            
        selected_upper = ans.selected_option.upper().strip()
        correct_upper = mcq.correct_option.upper().strip()
        is_correct = (selected_upper == correct_upper)
        
        if is_correct:
            correct_count += 1
            
        ans_record = AttemptAnswer(
            quiz_attempt_id=attempt.id,
            mcq_id=mcq.id,
            selected_option=ans.selected_option,
            is_correct=is_correct
        )
        db.add(ans_record)
        attempt_answers.append(ans_record)
        
    attempt.score = correct_count
    attempt.completed_at = datetime.utcnow()
    db.commit()
    db.refresh(attempt)

    # Retention engine: log each answer with its confidence; wrong or guessed
    # answers get a concept card (built in the background) and a re-test schedule.
    from app.retention import record_answer

    for ans in req.answers:
        mcq = mcq_map.get(ans.mcq_id)
        if mcq is not None:
            try:
                record_answer(db, current_user.id, mcq, ans.selected_option, ans.confidence or "sure", source="quiz")
            except Exception:
                logger.exception("Retention logging failed for MCQ %s", ans.mcq_id)
                db.rollback()
    
    return {
        "score": attempt.score,
        "total_questions": attempt.total_questions,
        "completed_at": attempt.completed_at
    }


@app.get("/api/quizzes/{attempt_id}")
def get_quiz_attempt(
    attempt_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_student_or_admin)
):
    """Retrieves the details of a previous quiz attempt, including all questions and selected answers."""
    attempt = db.query(QuizAttempt).filter(
        QuizAttempt.id == attempt_id,
        QuizAttempt.user_id == current_user.id
    ).first()
    
    if not attempt:
        raise HTTPException(status_code=404, detail="Quiz attempt not found.")
        
    answers = db.query(AttemptAnswer).filter(AttemptAnswer.quiz_attempt_id == attempt.id).all()
    
    mcqs_data = []
    selected_answers = {}
    
    for ans in answers:
        mcq = db.query(MCQ).filter(MCQ.id == ans.mcq_id).first()
        if mcq:
            mcqs_data.append({
                "id": mcq.id,
                "main_category": mcq.main_category,
                "sub_category": mcq.sub_category,
                "question_text": mcq.question_text,
                "options": mcq.options,
                "correct_option": mcq.correct_option
            })
            selected_answers[mcq.id] = ans.selected_option
            
    return {
        "id": attempt.id,
        "started_at": attempt.started_at.isoformat() if attempt.started_at else None,
        "completed_at": attempt.completed_at.isoformat() if attempt.completed_at else None,
        "score": attempt.score,
        "total_questions": attempt.total_questions,
        "questions": mcqs_data,
        "selected_answers": selected_answers,
        "timer_mode": attempt.timer_mode,
        "timer_value": attempt.timer_value,
        "feedback_mode": attempt.feedback_mode
    }


@app.get("/api/quiz/wrong")
def get_wrong_questions(
    db: Session = Depends(get_db),
    current_user: User = Depends(require_student_or_admin),
):
    """Returns the user's previously-missed MCQs (most recently missed first) for drill practice."""
    wrong_answers = (
        db.query(AttemptAnswer)
        .join(QuizAttempt, AttemptAnswer.quiz_attempt_id == QuizAttempt.id)
        .filter(
            QuizAttempt.user_id == current_user.id,
            AttemptAnswer.is_correct.is_(False),
        )
        .order_by(AttemptAnswer.id.desc())
        .limit(200)
        .all()
    )

    mcq_ids: list[int] = []
    seen: set[int] = set()
    for wa in wrong_answers:
        if wa.mcq_id not in seen:
            seen.add(wa.mcq_id)
            mcq_ids.append(wa.mcq_id)

    if not mcq_ids:
        return {"questions": [], "total": 0}

    mcqs = db.query(MCQ).filter(MCQ.id.in_(mcq_ids)).all()
    mcq_map = {m.id: m for m in mcqs}

    questions = []
    for mid in mcq_ids:
        m = mcq_map.get(mid)
        if not m:
            continue
        questions.append({
            "id": m.id,
            "question_text": m.question_text,
            "options": m.options,
            "correct_option": m.correct_option,
            "explanation_markdown": m.explanation_markdown,
            "topic": m.topic,
            "difficulty": m.difficulty,
        })

    return {"questions": questions, "total": len(questions)}


# ======================== CONVERSATIONAL CHAT HISTORY ========================

def _run_chat_turn(db: Session, req: "ChatQueryRequest", user_id: int, on_stage=None) -> dict:
    """One chat turn: resolve/create the conversation, answer with history, persist both messages."""
    conv_id = req.conversation_id
    if not conv_id:
        # Create a new conversation and auto-title based on the query prefix
        words = req.query.strip().split()
        title_text = " ".join(words[:6]) + ("..." if len(words) > 6 else "")
        conv = ChatConversation(user_id=user_id, title=title_text or "New Conversation")
        db.add(conv)
        db.commit()
        db.refresh(conv)
        conv_id = conv.id
    else:
        conv = db.query(ChatConversation).filter(
            ChatConversation.id == conv_id,
            ChatConversation.user_id == user_id
        ).first()
        if not conv:
            raise HTTPException(status_code=404, detail="Conversation session not found.")

    # Retrieve message history to maintain conversational memory (last 10 turns max)
    db_messages = db.query(ChatMessage).filter(
        ChatMessage.conversation_id == conv_id
    ).order_by(ChatMessage.created_at.asc()).all()

    history = []
    for msg in db_messages[-10:]:
        if msg.role == "user":
            history.append({"role": "user", "content": msg.content or ""})
        elif msg.role == "ai":
            ans_text = ""
            if msg.answer_json:
                try:
                    ans_text = json.loads(msg.answer_json).get("answer_markdown", "")
                except (ValueError, AttributeError):
                    pass
            history.append({"role": "assistant", "content": ans_text or msg.content or ""})

    answer_dict = generate_answer(
        session=db,
        query=req.query,
        confidence_threshold=req.confidence_threshold,
        history=history,
        book_id=req.book_id,
        chapter=req.chapter,
        level=req.level,
        on_stage=on_stage,
    )

    db.add(ChatMessage(conversation_id=conv_id, role="user", content=req.query))
    db.add(ChatMessage(
        conversation_id=conv_id,
        role="ai",
        content=answer_dict.get("answer_markdown", ""),
        answer_json=json.dumps(answer_dict),
    ))
    conv.updated_at = datetime.utcnow()
    db.commit()

    return {
        "conversation_id": conv_id,
        "conversation_title": conv.title,
        "answer": answer_dict
    }


@app.post("/api/chat/query")
def chat_query_endpoint(
    req: ChatQueryRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_student_or_admin)
):
    """Conversational RAG query answering endpoint that tracks message logs in DB and maintains LLM memory context."""
    return _run_chat_turn(db, req, current_user.id)


SSE_HEARTBEAT_S = 3.0


def _sse(event: str, data: Any) -> str:
    return f"event: {event}\ndata: {json.dumps(data)}\n\n"


@app.post("/api/chat/query/stream")
def chat_query_stream_endpoint(
    req: ChatQueryRequest,
    current_user: User = Depends(require_student_or_admin)
):
    """Same as /api/chat/query, streamed as Server-Sent Events.

    Progress events ("stage") arrive as the pipeline advances and a comment
    heartbeat is sent every few seconds, so no proxy or browser ever sees an
    idle connection while retrieval and the AI call run. The final event is
    "answer" (the /api/chat/query payload) or "error" ({"detail", "status"}).
    """
    import queue
    import threading

    user_id = current_user.id
    events: "queue.Queue[tuple[str, Any]]" = queue.Queue()

    def work() -> None:
        # Own session: request-scoped dependencies are closed before a
        # streaming body runs.
        db = SessionLocal()
        try:
            result = _run_chat_turn(db, req, user_id, on_stage=lambda stage: events.put(("stage", {"stage": stage})))
            events.put(("answer", result))
        except HTTPException as e:
            events.put(("error", {"detail": e.detail, "status": e.status_code}))
        except Exception as e:
            logger.exception("Streaming chat turn failed")
            events.put(("error", {"detail": f"Internal error: {e}", "status": 500}))
        finally:
            db.close()

    def stream():
        worker = threading.Thread(target=work, daemon=True)
        worker.start()
        yield _sse("stage", {"stage": "received"})
        while True:
            try:
                event, data = events.get(timeout=SSE_HEARTBEAT_S)
            except queue.Empty:
                yield ": keep-alive\n\n"
                continue
            yield _sse(event, data)
            if event in ("answer", "error"):
                break

    return StreamingResponse(
        stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache, no-transform", "X-Accel-Buffering": "no"},
    )


@app.get("/api/chat/source/{chunk_id}")
def get_chat_source_full(
    chunk_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_student_or_admin)
):
    """Returns the full text of a retrieved source chunk for the expandable sources panel."""
    chunk = db.query(Chunk).filter(Chunk.id == chunk_id).first()
    if not chunk:
        raise HTTPException(status_code=404, detail="Chunk not found.")
    return {
        "chunk_id": chunk.id,
        "book_title": chunk.book.title if chunk.book else "Unknown Textbook",
        "chapter": chunk.chapter,
        "page_number": chunk.page_number,
        "content": chunk.content,
    }


@app.get("/api/chat/conversations")
def get_user_conversations(
    db: Session = Depends(get_db),
    current_user: User = Depends(require_student_or_admin)
):
    """Returns the user's active chat conversations list ordered by recent updates."""
    conversations = db.query(ChatConversation).filter(
        ChatConversation.user_id == current_user.id
    ).order_by(ChatConversation.updated_at.desc()).all()
    
    return [
        {
            "id": c.id,
            "title": c.title,
            "updated_at": c.updated_at.isoformat() if c.updated_at else None
        }
        for c in conversations
    ]


@app.get("/api/chat/conversations/{conversation_id}")
def get_conversation_history(
    conversation_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_student_or_admin)
):
    """Retrieves all chat messages for a specific conversation session."""
    conv = db.query(ChatConversation).filter(
        ChatConversation.id == conversation_id,
        ChatConversation.user_id == current_user.id
    ).first()
    
    if not conv:
        raise HTTPException(status_code=404, detail="Conversation not found.")
        
    db_messages = db.query(ChatMessage).filter(
        ChatMessage.conversation_id == conversation_id
    ).order_by(ChatMessage.created_at.asc()).all()
    
    formatted_messages = []
    for msg in db_messages:
        answer_data = None
        if msg.answer_json:
            try:
                answer_data = json.loads(msg.answer_json)
            except:
                pass
                
        formatted_messages.append({
            "id": f"msg-{msg.id}",
            "type": "user" if msg.role == "user" else ("error" if msg.role == "error" else "ai"),
            "content": msg.content,
            "answer": answer_data,
            "timestamp": msg.created_at.strftime("%I:%M %p") if msg.created_at else None
        })
        
    return {
        "id": conv.id,
        "title": conv.title,
        "messages": formatted_messages
    }


@app.delete("/api/chat/conversations/{conversation_id}")
def delete_conversation(
    conversation_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_student_or_admin)
):
    """Deletes a chat conversation thread and all its messages."""
    conv = db.query(ChatConversation).filter(
        ChatConversation.id == conversation_id,
        ChatConversation.user_id == current_user.id
    ).first()
    
    if not conv:
        raise HTTPException(status_code=404, detail="Conversation not found.")
        
    db.delete(conv)
    db.commit()
    
    return {"message": "Conversation deleted successfully."}


# ======================== MCQ BANK & BOOKMARKS ========================

@app.get("/api/mcqs")
def get_all_mcqs(
    category: str | None = None,
    search: str | None = None,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_student_or_admin)
):
    """Retrieves list of all MCQs inside the database with category filters, searches, and bookmark indicators."""
    query = db.query(MCQ)
    if category and category != "all":
        query = query.filter(MCQ.main_category == category)
    if search:
        query = query.filter(MCQ.question_text.ilike(f"%{search}%"))
        
    mcqs = query.limit(100).all()
    
    # Fetch user's bookmarked MCQ IDs
    bookmarks = db.query(MCQBookmark.mcq_id).filter(MCQBookmark.user_id == current_user.id).all()
    bookmarked_ids = {b[0] for b in bookmarks}
    
    return [
        {
            "id": m.id,
            "main_category": m.main_category,
            "sub_category": m.sub_category,
            "question_text": m.question_text,
            "options": m.options,
            "correct_option": m.correct_option,
            "bookmarked": m.id in bookmarked_ids
        }
        for m in mcqs
    ]


@app.post("/api/bookmarks/mcq/{mcq_id}")
def toggle_mcq_bookmark(
    mcq_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_student_or_admin)
):
    """Toggles bookmark status of an MCQ for the current student."""
    mcq = db.query(MCQ).filter(MCQ.id == mcq_id).first()
    if not mcq:
        raise HTTPException(status_code=404, detail="MCQ not found.")
        
    existing = db.query(MCQBookmark).filter(
        MCQBookmark.user_id == current_user.id,
        MCQBookmark.mcq_id == mcq_id
    ).first()
    
    if existing:
        db.delete(existing)
        db.commit()
        return {"bookmarked": False}
    else:
        bookmark = MCQBookmark(user_id=current_user.id, mcq_id=mcq_id)
        db.add(bookmark)
        db.commit()
        return {"bookmarked": True}


@app.get("/api/bookmarks/mcq")
def get_mcq_bookmarks(
    db: Session = Depends(get_db),
    current_user: User = Depends(require_student_or_admin)
):
    """Returns the list of questions bookmarked by the user."""
    bookmarks = db.query(MCQBookmark).filter(MCQBookmark.user_id == current_user.id).all()
    return [
        {
            "id": b.mcq.id,
            "main_category": b.mcq.main_category,
            "sub_category": b.mcq.sub_category,
            "question_text": b.mcq.question_text,
            "options": b.mcq.options,
            "correct_option": b.mcq.correct_option,
            "bookmarked": True
        }
        for b in bookmarks if b.mcq
    ]


@app.post("/api/bookmarks/concept")
def create_concept_bookmark(
    req: ConceptBookmarkCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_student_or_admin)
):
    """Creates a persistent bookmark for textbook lines, RAG chatbot answers, or question concepts."""
    if not req.content.strip():
        raise HTTPException(status_code=400, detail="Content cannot be empty.")
        
    bookmark = ConceptBookmark(
        user_id=current_user.id,
        content=req.content,
        book_title=req.book_title,
        page_number=req.page_number,
        source_context=req.source_context
    )
    db.add(bookmark)
    db.commit()
    db.refresh(bookmark)
    return {
        "id": bookmark.id,
        "content": bookmark.content,
        "book_title": bookmark.book_title,
        "page_number": bookmark.page_number,
        "source_context": bookmark.source_context,
        "created_at": bookmark.created_at.isoformat()
    }


@app.get("/api/bookmarks/concept")
def get_concept_bookmarks(
    db: Session = Depends(get_db),
    current_user: User = Depends(require_student_or_admin)
):
    """Lists all the saved textbook concepts, citation selections, or RAG answers."""
    bookmarks = db.query(ConceptBookmark).filter(
        ConceptBookmark.user_id == current_user.id
    ).order_by(ConceptBookmark.created_at.desc()).all()
    
    return [
        {
            "id": b.id,
            "content": b.content,
            "book_title": b.book_title,
            "page_number": b.page_number,
            "source_context": b.source_context,
            "created_at": b.created_at.strftime("%b %d, %Y %I:%M %p") if b.created_at else None
        }
        for b in bookmarks
    ]


@app.delete("/api/bookmarks/concept/{bookmark_id}")
def delete_concept_bookmark(
    bookmark_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_student_or_admin)
):
    """Deletes a saved concept/text bookmark."""
    bookmark = db.query(ConceptBookmark).filter(
        ConceptBookmark.id == bookmark_id,
        ConceptBookmark.user_id == current_user.id
    ).first()
    if not bookmark:
        raise HTTPException(status_code=404, detail="Bookmark not found.")
    db.delete(bookmark)
    db.commit()
    return {"message": "Concept bookmark deleted successfully."}


# ======================== DETAILED TELEMETRY STATS ========================

@app.get("/api/dashboard/detailed-stats")
def get_detailed_stats(
    db: Session = Depends(get_db),
    current_user: User = Depends(require_student_or_admin)
):
    """Fetches comprehensive mock attempts stats, category accuracies, and attempt trends for Chart.js dashboard charts."""
    attempts = db.query(QuizAttempt).filter(
        QuizAttempt.user_id == current_user.id,
        QuizAttempt.completed_at != None
    ).order_by(QuizAttempt.started_at.asc()).all()
    
    total_attempts = len(attempts)
    if total_attempts == 0:
        return {
            "total_attempts": 0,
            "avg_accuracy": 0,
            "total_questions": 0,
            "history_trend": [],
            "category_breakdown": {}
        }
        
    total_score = sum(a.score for a in attempts if a.score)
    total_questions = sum(a.total_questions for a in attempts if a.total_questions)
    avg_accuracy = round((total_score / total_questions) * 100, 1) if total_questions > 0 else 0
    
    # Accuracy trend over time (last 15 completed mock sessions)
    trend = []
    for a in attempts[-15:]:
        acc = round((a.score / a.total_questions) * 100, 1) if a.total_questions and a.total_questions > 0 else 0
        date_str = a.started_at.strftime("%b %d") if a.started_at else "N/A"
        trend.append({
            "attempt_id": a.id,
            "date": date_str,
            "accuracy": acc,
            "score": a.score,
            "total": a.total_questions
        })
        
    # Group and count correct answers and totals per subject category
    category_breakdown = {}
    answers_query = db.query(AttemptAnswer.is_correct, MCQ.main_category).join(
        MCQ, MCQ.id == AttemptAnswer.mcq_id
    ).join(
        QuizAttempt, QuizAttempt.id == AttemptAnswer.quiz_attempt_id
    ).filter(
        QuizAttempt.user_id == current_user.id,
        QuizAttempt.completed_at != None
    ).all()
    
    for is_correct, cat in answers_query:
        category_name = cat if cat else "General"
        if category_name not in category_breakdown:
            category_breakdown[category_name] = {"correct": 0, "total": 0}
        category_breakdown[category_name]["total"] += 1
        if is_correct:
            category_breakdown[category_name]["correct"] += 1
            
    formatted_breakdown = {}
    for cat, stats in category_breakdown.items():
        formatted_breakdown[cat] = {
            "total_questions": stats["total"],
            "correct_answers": stats["correct"],
            "accuracy": round((stats["correct"] / stats["total"]) * 100, 1)
        }
        
    return {
        "total_attempts": total_attempts,
        "avg_accuracy": avg_accuracy,
        "total_questions": total_questions,
        "history_trend": trend,
        "category_breakdown": formatted_breakdown
    }


# ======================== STUDY: NOTES & FLASHCARDS ========================
# Personal study material is strictly per-user (shared MCQ bank stays global).

class NoteCreateRequest(BaseModel):
    title: str = "Untitled Note"
    content: str
    book_title: str | None = None
    page_number: int | None = None
    source_context: str | None = None


class FlashcardCreateRequest(BaseModel):
    front: str
    back: str
    topic: str | None = None
    book_title: str | None = None
    page_number: int | None = None


@app.get("/api/notes")
def list_notes(
    db: Session = Depends(get_db),
    current_user: User = Depends(require_student_or_admin),
):
    """Lists the current user's study notes (newest first)."""
    notes = db.query(Note).filter(Note.user_id == current_user.id).order_by(Note.updated_at.desc()).all()
    return [
        {
            "id": n.id,
            "title": n.title,
            "content": n.content,
            "book_title": n.book_title,
            "page_number": n.page_number,
            "source_context": n.source_context,
            "created_at": n.created_at.isoformat() if n.created_at else None,
            "updated_at": n.updated_at.isoformat() if n.updated_at else None,
        }
        for n in notes
    ]


@app.post("/api/notes", status_code=status.HTTP_201_CREATED)
def create_note(
    req: NoteCreateRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_student_or_admin),
):
    """Creates a new study note for the current user."""
    if not req.content.strip():
        raise HTTPException(status_code=400, detail="Note content cannot be empty.")
    note = Note(
        user_id=current_user.id,
        title=req.title.strip() or "Untitled Note",
        content=req.content.strip(),
        book_title=req.book_title,
        page_number=req.page_number,
        source_context=req.source_context,
    )
    db.add(note)
    db.commit()
    db.refresh(note)
    return {
        "id": note.id,
        "title": note.title,
        "content": note.content,
        "book_title": note.book_title,
        "page_number": note.page_number,
        "source_context": note.source_context,
        "created_at": note.created_at.isoformat() if note.created_at else None,
        "updated_at": note.updated_at.isoformat() if note.updated_at else None,
    }


@app.put("/api/notes/{note_id}")
def update_note(
    note_id: int,
    req: NoteCreateRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_student_or_admin),
):
    """Updates an existing note (title/content/source metadata)."""
    note = db.query(Note).filter(Note.id == note_id, Note.user_id == current_user.id).first()
    if not note:
        raise HTTPException(status_code=404, detail="Note not found.")
    note.title = req.title.strip() or note.title
    note.content = req.content.strip()
    note.book_title = req.book_title
    note.page_number = req.page_number
    note.source_context = req.source_context
    db.commit()
    db.refresh(note)
    return {
        "id": note.id,
        "title": note.title,
        "content": note.content,
        "book_title": note.book_title,
        "page_number": note.page_number,
        "source_context": note.source_context,
        "updated_at": note.updated_at.isoformat() if note.updated_at else None,
    }


@app.delete("/api/notes/{note_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_note(
    note_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_student_or_admin),
):
    """Deletes the current user's note."""
    note = db.query(Note).filter(Note.id == note_id, Note.user_id == current_user.id).first()
    if not note:
        raise HTTPException(status_code=404, detail="Note not found.")
    db.delete(note)
    db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@app.get("/api/flashcards")
def list_flashcards(
    db: Session = Depends(get_db),
    current_user: User = Depends(require_student_or_admin),
):
    """Lists the current user's flashcards (not-yet-due ones first for review sessions)."""
    due_first = _due_flashcard_sorter(db, current_user)
    return [
        {
            "id": f.id,
            "front": f.front,
            "back": f.back,
            "topic": f.topic,
            "book_title": f.book_title,
            "page_number": f.page_number,
            "box": f.box,
            "review_count": f.review_count,
            "last_reviewed": f.last_reviewed.isoformat() if f.last_reviewed else None,
            "next_due": f.next_due.isoformat() if f.next_due else None,
            "created_at": f.created_at.isoformat() if f.created_at else None,
        }
        for f in due_first
    ]


@app.get("/api/flashcards/review")
def get_flashcards_for_review(
    db: Session = Depends(get_db),
    current_user: User = Depends(require_student_or_admin),
):
    """Returns cards that are due now (plus a few new ones) for a review session."""
    now = datetime.utcnow()
    due = (
        db.query(Flashcard)
        .filter(
            Flashcard.user_id == current_user.id,
            (Flashcard.next_due.is_(None)) | (Flashcard.next_due <= now),
        )
        .order_by(Flashcard.next_due.asc().nulls_first())
        .limit(50)
        .all()
    )
    return [
        {
            "id": f.id,
            "front": f.front,
            "back": f.back,
            "topic": f.topic,
            "box": f.box,
            "review_count": f.review_count,
        }
        for f in due
    ]


@app.post("/api/flashcards", status_code=status.HTTP_201_CREATED)
def create_flashcard(
    req: FlashcardCreateRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_student_or_admin),
):
    """Creates a new flip-card for the current user."""
    if not req.front.strip() or not req.back.strip():
        raise HTTPException(status_code=400, detail="Both the front and back of a flashcard are required.")
    card = Flashcard(
        user_id=current_user.id,
        front=req.front.strip(),
        back=req.back.strip(),
        topic=req.topic,
        book_title=req.book_title,
        page_number=req.page_number,
    )
    db.add(card)
    db.commit()
    db.refresh(card)
    return {
        "id": card.id,
        "front": card.front,
        "back": card.back,
        "topic": card.topic,
        "book_title": card.book_title,
        "page_number": card.page_number,
        "box": card.box,
    }


@app.put("/api/flashcards/{card_id}")
def update_flashcard(
    card_id: int,
    req: FlashcardCreateRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_student_or_admin),
):
    """Edits the front/back/metadata of an existing card."""
    card = db.query(Flashcard).filter(
        Flashcard.id == card_id, Flashcard.user_id == current_user.id
    ).first()
    if not card:
        raise HTTPException(status_code=404, detail="Flashcard not found.")
    card.front = req.front.strip() or card.front
    card.back = req.back.strip() or card.back
    card.topic = req.topic
    card.book_title = req.book_title
    card.page_number = req.page_number
    db.commit()
    db.refresh(card)
    return {"id": card.id, "front": card.front, "back": card.back, "topic": card.topic}


@app.delete("/api/flashcards/{card_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_flashcard(
    card_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_student_or_admin),
):
    """Deletes the current user's flashcard."""
    card = db.query(Flashcard).filter(
        Flashcard.id == card_id, Flashcard.user_id == current_user.id
    ).first()
    if not card:
        raise HTTPException(status_code=404, detail="Flashcard not found.")
    db.delete(card)
    db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


class FlashcardReviewRequest(BaseModel):
    """Result of a single card flip: 0=again, 1=hard, 2=good, 3=easy."""

    rating: int


@app.post("/api/flashcards/{card_id}/review")
def review_flashcard(
    card_id: int,
    req: FlashcardReviewRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_student_or_admin),
):
    """Applies bounded spaced repetition (Leitner-style) after a card flip.

    Boxes 0..3 map to intervals of ~Incorrect / 1d / 3d / 7d. Ratings again/hard
    demote or hold; good/easy promote. Intervals are deliberately modest so the
    reviewer re-trips the biggest gaps within a week.
    """
    if req.rating not in (0, 1, 2, 3):
        raise HTTPException(status_code=400, detail="rating must be 0 (again), 1 (hard), 2 (good), or 3 (easy).")
    card = db.query(Flashcard).filter(
        Flashcard.id == card_id, Flashcard.user_id == current_user.id
    ).first()
    if not card:
        raise HTTPException(status_code=404, detail="Flashcard not found.")

    now = datetime.utcnow()
    if req.rating == 0:  # forgot it — back to box 0
        card.box = 0
        card.next_due = now + timedelta(minutes=10)
    elif req.rating == 1:  # hard — same box, sooner
        card.box = max(card.box - 1, 0)
        card.next_due = now + timedelta(days=1)
    else:
        card.box = min(card.box + 1, 3)
        interval_days = {1: 1, 2: 3, 3: 7}[card.box]
        card.next_due = now + timedelta(days=interval_days)

    card.last_reviewed = now
    card.review_count = (card.review_count or 0) + 1
    db.commit()
    db.refresh(card)
    return {"id": card.id, "box": card.box, "next_due": card.next_due.isoformat(), "review_count": card.review_count}


def _due_flashcard_sorter(db: Session, current_user: User) -> list[Flashcard]:
    """Order cards for the list view: due next, then unreviewed, then newest."""
    now = datetime.utcnow()
    cards = db.query(Flashcard).filter(Flashcard.user_id == current_user.id).all()

    def sort_key(c: Flashcard) -> tuple[int, int]:
        # Due-first: 0 = due, 1 = new (unreviewed), 2 = scheduled later; newest id last-breaks.
        if c.next_due is not None and c.next_due <= now:
            return (0, c.next_due.timestamp())
        if c.review_count == 0 or c.next_due is None:
            return (1, c.id)
        return (2, c.next_due.timestamp())

    return sorted(cards, key=sort_key)


# ======================== EXPORT ========================

@app.get("/api/export/mcqs")
def export_mcqs_csv(
    db: Session = Depends(get_db),
    current_user: User = Depends(require_student_or_admin),
):
    """Download the MCQ bank as a CSV (global shared bank — any student may export)."""
    import csv
    import io

    mcqs = db.query(MCQ).order_by(MCQ.id.asc()).all()
    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow([
        "id", "quiz_set_title", "topic", "main_category", "sub_category",
        "difficulty", "question_text", "option_a", "option_b", "option_c",
        "option_d", "correct_option", "explanation_markdown",
    ])
    for m in mcqs:
        opts = m.options if isinstance(m.options, dict) else {}
        writer.writerow([
            m.id, m.quiz_set_title, m.topic, m.main_category, m.sub_category,
            m.difficulty if m.difficulty is not None else "",
            m.question_text,
            opts.get("A", ""), opts.get("B", ""), opts.get("C", ""), opts.get("D", ""),
            m.correct_option,
            (m.explanation_markdown or "").replace("\n", " "),
        ])
    csv_content = "\ufeff" + buf.getvalue()  # BOM so Excel renders UTF-8 correctly
    return Response(
        content=csv_content,
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": 'attachment; filename="mednama_mcq_bank.csv"'},
    )


@app.get("/api/export/notes")
def export_notes_csv(
    db: Session = Depends(get_db),
    current_user: User = Depends(require_student_or_admin),
):
    """Download the current user's study notes as a CSV."""
    import csv
    import io

    notes = db.query(Note).filter(Note.user_id == current_user.id).order_by(Note.updated_at.desc()).all()
    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(["id", "title", "content", "book_title", "page_number", "source_context", "updated_at"])
    for n in notes:
        writer.writerow([
            n.id, n.title, (n.content or "").replace("\n", " "),
            n.book_title or "", n.page_number or "",
            n.source_context or "",
            n.updated_at.strftime("%Y-%m-%d %H:%M") if n.updated_at else "",
        ])
    csv_content = "\ufeff" + buf.getvalue()
    return Response(
        content=csv_content,
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": 'attachment; filename="mednama_notes.csv"'},
    )


@app.get("/api/export/bookmarks")
def export_bookmarks_csv(
    db: Session = Depends(get_db),
    current_user: User = Depends(require_student_or_admin),
):
    """Download the current user's concept bookmarks as a CSV."""
    import csv
    import io

    bookmarks = db.query(ConceptBookmark).filter(
        ConceptBookmark.user_id == current_user.id
    ).order_by(ConceptBookmark.created_at.desc()).all()

    mcq_bookmarks = db.query(MCQBookmark).filter(
        MCQBookmark.user_id == current_user.id
    ).order_by(MCQBookmark.created_at.desc()).all()
    mcq_map = {b.mcq_id: b for b in mcq_bookmarks}
    mcqs = db.query(MCQ).filter(MCQ.id.in_(list(mcq_map.keys()))).all() if mcq_map else []
    mcq_text = {m.id: m.question_text for m in mcqs}

    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(["type", "content", "book_title", "page_number", "created_at"])
    for b in bookmarks:
        writer.writerow(["concept", (b.content or "").replace("\n", " "), b.book_title or "", b.page_number or "", b.created_at.strftime("%Y-%m-%d %H:%M") if b.created_at else ""])
    for mcq_mark, m in mcq_map.items():
        writer.writerow(["mcq", (mcq_text.get(mcq_mark) or "").replace("\n", " "), "", "", m.created_at.strftime("%Y-%m-%d %H:%M") if m.created_at else ""])

    csv_content = "\ufeff" + buf.getvalue()
    return Response(
        content=csv_content,
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": 'attachment; filename="mednama_bookmarks.csv"'},
    )




# ─── Answer reports ("Report wrong answer") ────────────────────────────────

class AnswerReportCreate(BaseModel):
    kind: str  # 'chat' | 'mcq'
    reason: str
    mcq_id: int | None = None
    question: str | None = None
    answer_excerpt: str | None = None


class AnswerReportUpdate(BaseModel):
    status: str  # 'open' | 'resolved' | 'dismissed'


def _serialize_report(r: AnswerReport) -> dict:
    return {
        "id": r.id,
        "kind": r.kind,
        "mcq_id": r.mcq_id,
        "question": r.question,
        "answer_excerpt": r.answer_excerpt,
        "reason": r.reason,
        "status": r.status,
        "username": r.user.username if r.user else None,
        "created_at": r.created_at.isoformat() if r.created_at else None,
        "resolved_at": r.resolved_at.isoformat() if r.resolved_at else None,
    }


@app.post(
    "/api/reports",
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(rate_limiter(limit=20, window=60))],
)
def create_answer_report(
    req: AnswerReportCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_student_or_admin),
):
    """Flag a chat answer or MCQ as wrong / badly cited / outdated for admin review."""
    if req.kind not in ("chat", "mcq"):
        raise HTTPException(status_code=400, detail="kind must be 'chat' or 'mcq'.")
    reason = (req.reason or "").strip()
    if not reason:
        raise HTTPException(status_code=400, detail="Please say what is wrong.")
    if req.kind == "mcq":
        if not req.mcq_id or not db.query(MCQ.id).filter(MCQ.id == req.mcq_id).first():
            raise HTTPException(status_code=404, detail="MCQ not found.")
    report = AnswerReport(
        user_id=current_user.id,
        kind=req.kind,
        mcq_id=req.mcq_id if req.kind == "mcq" else None,
        question=(req.question or "")[:2000] or None,
        answer_excerpt=(req.answer_excerpt or "")[:4000] or None,
        reason=reason[:2000],
    )
    db.add(report)
    db.commit()
    db.refresh(report)
    logger.info("Answer report %d filed by %s (%s)", report.id, current_user.username, req.kind)
    return _serialize_report(report)


@app.get("/api/reports")
def list_answer_reports(
    status_filter: str = "open",
    db: Session = Depends(get_db),
    current_user: User = Depends(require_admin),
):
    """Admin review queue. status_filter: open | resolved | dismissed | all."""
    q = db.query(AnswerReport).options(joinedload(AnswerReport.user))
    if status_filter != "all":
        q = q.filter(AnswerReport.status == status_filter)
    return [_serialize_report(r) for r in q.order_by(AnswerReport.created_at.desc()).limit(200).all()]


@app.patch("/api/reports/{report_id}")
def update_answer_report(
    report_id: int,
    req: AnswerReportUpdate,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_admin),
):
    """Mark a report resolved / dismissed (or reopen it)."""
    if req.status not in ("open", "resolved", "dismissed"):
        raise HTTPException(status_code=400, detail="status must be open, resolved or dismissed.")
    report = db.query(AnswerReport).filter(AnswerReport.id == report_id).first()
    if not report:
        raise HTTPException(status_code=404, detail="Report not found.")
    report.status = req.status
    report.resolved_at = None if req.status == "open" else datetime.utcnow()
    db.commit()
    db.refresh(report)
    return _serialize_report(report)


# ─── Daily loop: Daily Dose, answers with confidence, concept cards, readiness ──

class StudyAnswerRequest(BaseModel):
    mcq_id: int
    selected_option: str
    confidence: str = "sure"          # sure | unsure | guess
    dose_index: int | None = None     # position in today's Daily Dose, if answered there


class ExamDateRequest(BaseModel):
    exam_date: str | None = None      # YYYY-MM-DD, or null to clear


def _mcq_payload(m: MCQ) -> dict:
    return {
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


@app.get("/api/study/daily")
def get_daily_dose(
    db: Session = Depends(get_db),
    current_user: User = Depends(require_student_or_admin),
):
    """Today's Daily Dose: due concept re-tests, new questions, a spot-the-diagnosis image, a pearl."""
    from app.models import ConceptCard
    from app.retention import get_or_build_daily_session, serialize_card, streak

    # current_user belongs to auth's own DB session; changes (streak freezes) must be
    # made on this request's session or they are never committed.
    user = db.get(User, current_user.id)
    session = get_or_build_daily_session(db, user)
    items = []
    for i, item in enumerate(session.items or []):
        entry = {**item, "index": i}
        if item.get("mcq_id"):
            m = db.get(MCQ, item["mcq_id"])
            entry["mcq"] = _mcq_payload(m) if m else None
        if item.get("type") == "pearl" and item.get("concept_id"):
            card = db.get(ConceptCard, item["concept_id"])
            entry["concept"] = serialize_card(db, card) if card else None
        items.append(entry)
    return {
        "day": session.day.isoformat(),
        "items": items,
        "completed": session.completed_at is not None,
        "streak": streak(db, current_user.id),
        "streak_freezes": user.streak_freezes,
    }


@app.post("/api/study/answer")
def study_answer(
    req: StudyAnswerRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_student_or_admin),
):
    """Answer one question with a confidence tap; schedules concept re-tests."""
    from app.models import ConceptCard, DailySession
    from app.retention import mark_item_done, record_answer, serialize_card
    from datetime import date as _date

    mcq = db.get(MCQ, req.mcq_id)
    if mcq is None:
        raise HTTPException(status_code=404, detail="Question not found.")
    source = "dose" if req.dose_index is not None else "practice"
    result = record_answer(db, current_user.id, mcq, req.selected_option, req.confidence, source=source)
    if req.dose_index is not None:
        session = db.query(DailySession).filter_by(user_id=current_user.id, day=_date.today()).first()
        if session is not None:
            mark_item_done(db, session, req.dose_index, result["is_correct"])
    card = db.get(ConceptCard, mcq.concept_id) if mcq.concept_id else None
    return {
        **result,
        "explanation_markdown": mcq.explanation_markdown,
        "concept": serialize_card(db, card) if card else None,
    }


@app.post("/api/study/dose/{index}/done")
def mark_dose_item_done(
    index: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_student_or_admin),
):
    """Mark a non-question Daily Dose item (e.g. the pearl) as done."""
    from app.models import DailySession
    from app.retention import mark_item_done, streak
    from datetime import date as _date

    session = db.query(DailySession).filter_by(user_id=current_user.id, day=_date.today()).first()
    if session is None:
        raise HTTPException(status_code=404, detail="No Daily Dose for today yet.")
    mark_item_done(db, session, index)
    return {"completed": session.completed_at is not None, "streak": streak(db, current_user.id)}


@app.get("/api/concepts/by-mcq/{mcq_id}")
def concept_for_mcq(
    mcq_id: int,
    response: Response,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_student_or_admin),
):
    """Concept card for a question. 202 while it is still being written in the background."""
    from app.retention import concept_status_for_mcq, ensure_concept_async, serialize_card

    state, card = concept_status_for_mcq(db, mcq_id)
    if state == "missing":
        raise HTTPException(status_code=404, detail="Question not found.")
    if state == "none":
        ensure_concept_async(mcq_id)
        state = "pending"
    if state == "pending":
        response.status_code = status.HTTP_202_ACCEPTED
        return {"status": "pending"}
    return {"status": "ready", "concept": serialize_card(db, card)}


@app.get("/api/concepts/{concept_id}")
def get_concept(
    concept_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_student_or_admin),
):
    from app.models import ConceptCard
    from app.retention import serialize_card

    card = db.get(ConceptCard, concept_id)
    if card is None:
        raise HTTPException(status_code=404, detail="Concept not found.")
    return serialize_card(db, card)


@app.get("/api/study/readiness")
def get_readiness(
    db: Session = Depends(get_db),
    current_user: User = Depends(require_student_or_admin),
):
    """Readiness estimate vs the 75% line, concept mastery, exam countdown, streak."""
    from app.retention import readiness

    return readiness(db, db.get(User, current_user.id))


@app.put("/api/study/exam-date")
def set_exam_date(
    req: ExamDateRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_student_or_admin),
):
    from datetime import date as _date

    user = db.get(User, current_user.id)   # this request's session, so the change is committed
    if req.exam_date:
        try:
            user.exam_date = _date.fromisoformat(req.exam_date)
        except ValueError:
            raise HTTPException(status_code=400, detail="exam_date must be YYYY-MM-DD.")
    else:
        user.exam_date = None
    db.commit()
    return {"exam_date": user.exam_date.isoformat() if user.exam_date else None}


# ─── Answer-Key Referee + private recall bank (admin-only until licensed) ─────

class RefereeRequest(BaseModel):
    question: str
    answer: str | None = None                 # recall style: "question = answer"
    options: dict[str, str] | None = None     # MCQ style
    key: str | None = None                    # published key for the MCQ, if any


class RecallReviewRequest(BaseModel):
    review_status: str                        # confirmed | corrected | rejected | unreviewed
    reviewer_note: str | None = None


def _serialize_recall(r) -> dict:
    return {
        "id": r.id, "source": r.source, "page": r.page, "chapter": r.chapter, "kind": r.kind,
        "headline_no": r.headline_no, "question": r.question, "answer": r.answer, "verdict": r.verdict,
        "textbook_answer": r.textbook_answer, "evidence": r.evidence or [], "explanation": r.explanation,
        "review_status": r.review_status, "reviewer_note": r.reviewer_note,
        "refereed_at": r.refereed_at.isoformat() if r.refereed_at else None,
    }


@app.post("/api/referee", dependencies=[Depends(rate_limiter(limit=30, window=60))])
def referee_question(
    req: RefereeRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_admin),
):
    """Check a recall answer or MCQ key against the textbooks (verified quotes only)."""
    from app.referee import judge

    if not (req.question or "").strip():
        raise HTTPException(status_code=400, detail="Question is required.")
    if not req.answer and not req.options:
        raise HTTPException(status_code=400, detail="Give the published answer, or the options (and key).")
    try:
        return judge(db, req.question, answer=req.answer, options=req.options, key=req.key)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@app.get("/api/recalls")
def list_recalls(
    verdict: str | None = None,
    chapter: str | None = None,
    q: str | None = None,
    review_status: str | None = None,
    offset: int = 0,
    limit: int = 50,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_admin),
):
    """Browse the private recall bank with verdicts (admin)."""
    from app.models import RecallItem

    query = db.query(RecallItem)
    if verdict == "unrefereed":
        query = query.filter(RecallItem.verdict.is_(None))
    elif verdict == "disputed":
        query = query.filter(RecallItem.verdict.in_(("contradicted", "books_conflict")))
    elif verdict:
        query = query.filter(RecallItem.verdict == verdict)
    if chapter:
        query = query.filter(RecallItem.chapter.ilike(f"%{chapter}%"))
    if review_status:
        query = query.filter(RecallItem.review_status == review_status)
    if q:
        like = f"%{q}%"
        query = query.filter((RecallItem.question.ilike(like)) | (RecallItem.answer.ilike(like)))
    total = query.count()
    rows = query.order_by(RecallItem.page, RecallItem.id).offset(max(0, offset)).limit(min(max(1, limit), 200)).all()
    counts = dict(db.query(RecallItem.verdict, func.count(RecallItem.id)).group_by(RecallItem.verdict).all())
    return {
        "total": total,
        "items": [_serialize_recall(r) for r in rows],
        "counts": {("unrefereed" if k is None else k): v for k, v in counts.items()},
        "chapters": [c for (c,) in db.query(RecallItem.chapter).distinct().order_by(RecallItem.chapter) if c],
    }


@app.post("/api/recalls/{recall_id}/referee")
def referee_recall(
    recall_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_admin),
):
    """(Re)run the referee for one recall and store the verdict."""
    from app.models import RecallItem
    from app.referee import judge

    item = db.get(RecallItem, recall_id)
    if item is None:
        raise HTTPException(status_code=404, detail="Recall not found.")
    r = judge(db, item.question, answer=item.answer)
    if not r.get("verdict"):
        raise HTTPException(status_code=502, detail=r.get("error") or "Referee failed.")
    item.verdict = r["verdict"]
    item.textbook_answer = r.get("textbook_answer")
    item.evidence = r.get("evidence")
    item.explanation = (r.get("explanation") or "") + (
        f"\n\nAI reasoning (not from the textbooks): {r['ai_reasoning']}" if r.get("ai_reasoning") else "")
    item.refereed_at = datetime.utcnow()
    db.commit()
    return {**_serialize_recall(item), "figures": r.get("figures", [])}


@app.patch("/api/recalls/{recall_id}")
def review_recall(
    recall_id: int,
    req: RecallReviewRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_admin),
):
    """A doctor's review of the verdict (confirmed / corrected / rejected)."""
    from app.models import RecallItem

    if req.review_status not in ("unreviewed", "confirmed", "corrected", "rejected"):
        raise HTTPException(status_code=400, detail="Invalid review_status.")
    item = db.get(RecallItem, recall_id)
    if item is None:
        raise HTTPException(status_code=404, detail="Recall not found.")
    item.review_status = req.review_status
    item.reviewer_note = (req.reviewer_note or "").strip()[:2000] or None
    db.commit()
    return _serialize_recall(item)


# ─── Explain it back ────────────────────────────────────────────────────────

class ExplainBackRequest(BaseModel):
    explanation: str


@app.post("/api/concepts/{concept_id}/explain-back", dependencies=[Depends(rate_limiter(limit=20, window=60))])
def explain_back(
    concept_id: int,
    req: ExplainBackRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_student_or_admin),
):
    """Mark the student's own explanation of a concept against the textbook passage."""
    from app.models import ConceptCard
    from app.retention import grade_explanation

    card = db.get(ConceptCard, concept_id)
    if card is None or (card.visibility != "all" and current_user.role != "admin"):
        raise HTTPException(status_code=404, detail="Concept not found.")
    try:
        return grade_explanation(db, current_user.id, card, req.explanation)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.exception("Explain-back grading failed")
        raise HTTPException(status_code=502, detail=f"Could not mark the explanation: {e}")


# ─── Challenge a friend (duels) ─────────────────────────────────────────────

class DuelCreateRequest(BaseModel):
    subject: str | None = None      # optional FCPS subject (e.g. Physiology)
    count: int = 10


class DuelSubmitRequest(BaseModel):
    answers: dict[str, str]         # mcq_id -> option letter
    time_ms: int | None = None


def _duel_results(db: Session, duel, viewer_id: int) -> dict:
    from app.models import DuelEntry

    entries = db.query(DuelEntry).options(joinedload(DuelEntry.user)).filter(DuelEntry.duel_id == duel.id) \
        .order_by(DuelEntry.score.desc(), DuelEntry.time_ms.asc().nullslast()).all()
    played = any(e.user_id == viewer_id for e in entries)
    mcqs = {m.id: m for m in db.query(MCQ).filter(MCQ.id.in_(duel.mcq_ids)).all()}
    questions = []
    for mid in duel.mcq_ids:
        m = mcqs.get(mid)
        if m is None:
            continue
        q = {"id": m.id, "question_text": m.question_text, "options": m.options, "figure_id": m.figure_id}
        if played:   # answers and explanations only after you have played
            q.update({"correct_option": m.correct_option, "explanation_markdown": m.explanation_markdown,
                      "picks": {e.user.username if e.user else str(e.user_id): (e.answers or {}).get(str(m.id))
                                for e in entries}})
        questions.append(q)
    return {
        "code": duel.code,
        "title": duel.title,
        "expires_at": duel.expires_at.isoformat(),
        "played": played,
        "total": len(questions),
        "players": [{"username": e.user.username if e.user else str(e.user_id), "score": e.score,
                     "time_ms": e.time_ms, "is_you": e.user_id == viewer_id} for e in entries],
        "questions": questions,
    }


@app.post("/api/duels", status_code=status.HTTP_201_CREATED, dependencies=[Depends(rate_limiter(limit=10, window=60))])
def create_duel(
    req: DuelCreateRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_student_or_admin),
):
    """Create a shareable 10-question challenge (public-bank questions only)."""
    import secrets
    from app.models import Duel
    from app.retention import _books_for_subject

    count = max(5, min(20, req.count or 10))
    q = db.query(MCQ.id).filter(MCQ.status == "ready", MCQ.figure_id.is_(None))
    book_ids = _books_for_subject(db, req.subject) if req.subject else []
    if book_ids:
        q = q.filter(MCQ.book_id.in_(book_ids))
    ids = [r[0] for r in q.order_by(func.random()).limit(count).all()]
    if len(ids) < 5:
        raise HTTPException(status_code=400, detail="Not enough questions in the bank for a duel yet.")
    duel = Duel(code=secrets.token_urlsafe(6).replace("-", "x").replace("_", "y"), creator_id=current_user.id,
                title=f"{req.subject or 'Mixed'} duel · {len(ids)} questions", mcq_ids=ids,
                expires_at=datetime.utcnow() + timedelta(days=7))
    db.add(duel)
    db.commit()
    return {"code": duel.code, "title": duel.title, "total": len(ids)}


@app.get("/api/duels/{code}")
def get_duel(
    code: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_student_or_admin),
):
    from app.models import Duel

    duel = db.query(Duel).filter(Duel.code == code).first()
    if duel is None:
        raise HTTPException(status_code=404, detail="Duel not found.")
    if duel.expires_at < datetime.utcnow():
        raise HTTPException(status_code=410, detail="This duel has expired.")
    return _duel_results(db, duel, current_user.id)


@app.post("/api/duels/{code}/submit")
def submit_duel(
    code: str,
    req: DuelSubmitRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_student_or_admin),
):
    """Submit your answers once; returns the side-by-side result."""
    from app.models import Duel, DuelEntry
    from app.retention import record_answer

    duel = db.query(Duel).filter(Duel.code == code).first()
    if duel is None:
        raise HTTPException(status_code=404, detail="Duel not found.")
    if duel.expires_at < datetime.utcnow():
        raise HTTPException(status_code=410, detail="This duel has expired.")
    if db.query(DuelEntry.id).filter_by(duel_id=duel.id, user_id=current_user.id).first():
        return _duel_results(db, duel, current_user.id)
    mcqs = {m.id: m for m in db.query(MCQ).filter(MCQ.id.in_(duel.mcq_ids)).all()}
    answers = {str(k): str(v).strip().upper()[:1] for k, v in (req.answers or {}).items() if int(k) in mcqs}
    score = sum(1 for mid, m in mcqs.items() if answers.get(str(mid)) == (m.correct_option or "").upper())
    db.add(DuelEntry(duel_id=duel.id, user_id=current_user.id, answers=answers, score=score,
                     time_ms=req.time_ms if req.time_ms and req.time_ms > 0 else None))
    db.commit()
    for mid, m in mcqs.items():   # duel answers feed the same retention engine
        if str(mid) in answers:
            try:
                record_answer(db, current_user.id, m, answers[str(mid)], "sure", source="duel")
            except Exception:
                logger.exception("Retention logging failed for duel MCQ %s", mid)
                db.rollback()
    return _duel_results(db, duel, current_user.id)


@app.get("/api/recalls/frequency")
def recall_frequency(
    db: Session = Depends(get_db),
    current_user: User = Depends(require_admin),
):
    """High-yield map: how often each chapter/system appears in the recall bank (headlines + variants)."""
    from sqlalchemy import case as _case
    from app.models import RecallItem

    rows = (
        db.query(
            RecallItem.chapter,
            func.count(RecallItem.id),
            func.sum(_case((RecallItem.kind == "headline", 1), else_=0)),
            func.sum(_case((RecallItem.verdict.in_(("contradicted", "books_conflict")), 1), else_=0)),
        )
        .group_by(RecallItem.chapter)
        .order_by(func.count(RecallItem.id).desc())
        .all()
    )
    # Page-level extraction labels the same system several ways; fold the obvious aliases together.
    aliases = {
        "gastrointestinal": "Gastroenterology", "hepatobiliary": "Gastroenterology", "hepatitis": "Gastroenterology",
        "respiratory": "Pulmonology", "calculation chapter": "Calculations", "cell physiology": "Cell Biology",
    }
    merged: dict[str, list[int]] = {}
    for chapter, n, h, d in rows:
        name = (chapter or "Unlabelled").strip()
        name = aliases.get(name.lower(), name)
        acc = merged.setdefault(name, [0, 0, 0])
        acc[0] += int(n)
        acc[1] += int(h or 0)
        acc[2] += int(d or 0)
    total = sum(v[0] for v in merged.values()) or 1
    return sorted(
        ({"chapter": name, "recalls": n, "headlines": h, "disputed": d, "share": round(n / total, 4)}
         for name, (n, h, d) in merged.items()),
        key=lambda x: -x["recalls"],
    )
