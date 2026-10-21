"""Seed Studio settings (environment variables) and the bridge to the backend.

Import this module first: it puts backend/ on the import path, so Seed Studio reuses medNAMA's database, retrieval,
LLM clients and validation instead of copying them.

    SEED_STUDIO_HOST          bind address (default 127.0.0.1: this PC only)
    SEED_STUDIO_PORT          default 8770
    SEED_STUDIO_WORKERS       batches processed at once (default 2; the LLM and the embedding model are the limits)
    SEED_STUDIO_PRICE_IN      USD per 1M input tokens for the generation model, for cost estimates (unset = no cost)
    SEED_STUDIO_PRICE_OUT     USD per 1M output tokens
    SEED_STUDIO_STALE_S       a running batch with no heartbeat for this long is treated as interrupted (default 600)

Database, LLM keys and models come from the root .env exactly as for the backend (DATABASE_URL, DEEPSEEK_*,
GEMINI_API_KEY): the same corpus and the same generator as the app.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

STUDIO_DIR = Path(__file__).resolve().parent
REPO = STUDIO_DIR.parent.parent       # labs/seed_studio -> repo root
BACKEND = REPO / "backend"
DATA = REPO / "data"
STATIC = STUDIO_DIR / "static"

HOST = os.environ.get("SEED_STUDIO_HOST", "127.0.0.1")
PORT = int(os.environ.get("SEED_STUDIO_PORT", "8770"))
WORKERS = max(1, int(os.environ.get("SEED_STUDIO_WORKERS", "2")))
STALE_S = int(os.environ.get("SEED_STUDIO_STALE_S", "600"))


def _price(name: str) -> float | None:
    v = os.environ.get(name, "").strip()
    try:
        return float(v) if v else None
    except ValueError:
        return None


PRICE_IN = _price("SEED_STUDIO_PRICE_IN")
PRICE_OUT = _price("SEED_STUDIO_PRICE_OUT")

# Rough token sizes per unit of work, for the preview's estimate only (actual usage is recorded per batch).
EST_TOKENS_IN_PER_BATCH = 6500      # instructions + up to 9k characters of textbook passages + the avoid list
EST_TOKENS_OUT_PER_QUESTION = 450   # stem, options, explanation, distractor rationales, objective
EST_TOKENS_IN_PER_NOTE = 7000
EST_TOKENS_OUT_PER_NOTE = 1200

os.environ.setdefault("HF_HUB_OFFLINE", "1")          # models from the local cache; never download mid-job
os.environ.setdefault("TRANSFORMERS_OFFLINE", "1")

if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))
