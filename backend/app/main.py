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


# ======================== ROUTES (app/routers/, one module per area) ========================
# Included in the order they were written: overlapping paths (e.g. /api/mocks/weekly/start vs
# /api/mocks/{mock_id}/start) must keep that order.
from app.routers import auth, books, query, chat, bank, stats, notes, export, reports, study, referee, duels, modes, pastpapers, rapid, pages, practice  # noqa: E402
from app.deps import get_db  # noqa: E402,F401  (kept importable from app.main)

for _module in (auth, books, query, chat, bank, stats, notes, export, reports, study, referee, duels, modes, pastpapers, rapid, pages, practice):
    app.include_router(_module.router)
