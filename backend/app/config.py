import os
from pathlib import Path
from pydantic_settings import BaseSettings


def find_env_file() -> str:
    # Look for .env in current, parent, or grandparent dir
    start_dir = Path(__file__).resolve().parent
    for parent in [start_dir, start_dir.parent, start_dir.parent.parent]:
        env_path = parent / ".env"
        if env_path.exists():
            return str(env_path)
    return ".env"


class Settings(BaseSettings):
    # Database
    database_url: str = "postgresql://medrag:medrag@localhost:5433/medrag"
    # Connection pool (one uvicorn process; Postgres allows 100). The old default (5 + 10) ran out when two
    # background AI jobs overlapped, and every other request - the library, stats - timed out with a 500.
    db_pool_size: int = 10
    db_max_overflow: int = 20
    db_pool_timeout_s: int = 20
    # Heavy background AI work (hardening, twists) running at once, across all users. Retrieval and the reranker
    # are CPU-bound: more than 2 at a time on the NAS only slows every job down. The PC can take 4.
    ai_job_workers: int = 2
    # Book PDFs, for the page viewer (books.filename is looked up here). "" = /app/pdfs in Docker,
    # else <repo>/pdfs. Rendered pages are cached under page_cache_dir ("" = <tmp>/mednama-pages).
    pdfs_dir: str = ""
    page_cache_dir: str = ""
    # Page images one user may open per hour (the viewer shows a few pages around a citation).
    page_views_per_hour: int = 300
    # Who may open book page images: "admin" (the library's owner) or "all". The books are copyrighted;
    # showing their pages to other people is distribution, so it is owner-only unless switched on.
    page_viewer_access: str = "admin"
    # Who can create an account: "invite" (a code from the admin's Users page), "open" or "closed" (the admin
    # creates accounts).
    registration_mode: str = "invite"

    # DeepSeek (Phase 4)
    deepseek_api_key: str = ""
    deepseek_base_url: str = "https://api.deepseek.com/v1"
    deepseek_model: str = "deepseek-v4-flash"
    # Per-call wall-clock limit and SDK retries. The SDK default (600 s, 2 retries)
    # let one slow upstream call outlive every proxy and browser timeout.
    llm_timeout_s: float = 60.0
    llm_max_retries: int = 1
    # deepseek-v4-flash reasons before answering unless told not to. Reasoning
    # multiplies latency and can eat the whole max_tokens budget, so it is off
    # unless explicitly enabled for chat answers.
    llm_thinking: bool = False
    # Optional per-job model overrides (any OpenAI-compatible endpoint, e.g. a
    # MedGemma server on vLLM or Vertex). Empty = use the DEEPSEEK_* settings.
    #   chat: answers and MCQ explanations    fast: query rewrite, MCQ generation
    llm_chat_model: str = ""
    llm_chat_base_url: str = ""
    llm_chat_api_key: str = ""
    llm_fast_model: str = ""
    llm_fast_base_url: str = ""
    llm_fast_api_key: str = ""
    # Rewrite exam-style questions into textbook search terms before retrieval.
    query_rewrite: bool = True
    # Daily Dose weights new questions toward topics past papers ask most, using
    # the private recall bank: "off", "admin" (private testing) or "all".
    high_yield_dose: str = "admin"
    # Imported past papers: who may see them. "all" (default) = every signed-in student;
    # "admin" = admins only. Shared features (duels, the weekly mock) never include them either way.
    past_papers_access: str = "all"
    # Two-stage reranking: the fast ms-marco model orders all candidates, then a
    # biomedical cross-encoder re-orders its top N (scripts/eval_rerankers.py).
    # Set RERANKER_SECOND_STAGE="" to disable.
    reranker_second_stage: str = "ncbi/MedCPT-Cross-Encoder"
    reranker_second_stage_top_n: int = 8

    # Logging (app loggers; uvicorn's own access log is separate)
    log_level: str = "INFO"

    # Gemini vision (Phase 2)
    gemini_api_key: str = ""

    # Auth (Phase 7)
    jwt_secret: str = "dev-secret-change-in-production"

    # Ingestion
    max_upload_size_mb: int = 500

    # AI quiz generation
    ai_mcq_difficulty: int | None = None  # default difficulty (1-5) when a request doesn't specify one

    # CORS
    allowed_origins: str = "http://localhost:3000,https://med-nama.vercel.app"

    model_config = {
        "env_file": find_env_file(), 
        "env_file_encoding": "utf-8",
        "extra": "ignore"
    }


settings = Settings()
# A plain postgresql:// URL means psycopg (v3) in SQLAlchemy 2.1+, but the image ships psycopg2: name the driver.
if settings.database_url.startswith("postgresql://"):
    settings.database_url = "postgresql+psycopg2://" + settings.database_url[len("postgresql://"):]
