"""PaedNAMA settings and the bridge to medNAMA's backend.

Import this module first: it puts backend/ on the import path, so PaedNAMA uses medNAMA's database, book library,
retrieval, LLM clients and station engine as a library. PaedNAMA never edits medNAMA's files; anything it needs to
change is copied into apps/paednama and marked "merge back".

    PAEDNAMA_HOST   bind address (default 127.0.0.1: this PC only)
    PAEDNAMA_PORT   default 8780

Database, LLM keys and models come from the repo root .env, exactly as for medNAMA.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

APP_DIR = Path(__file__).resolve().parents[1]          # apps/paednama
REPO = APP_DIR.parents[1]
BACKEND = REPO / "backend"
REGISTRY = APP_DIR / "data" / "cpsp_paeds.json"

HOST = os.environ.get("PAEDNAMA_HOST", "127.0.0.1")
PORT = int(os.environ.get("PAEDNAMA_PORT", "8780"))

os.environ.setdefault("HF_HUB_OFFLINE", "1")           # models from the local cache; never download mid-request
os.environ.setdefault("TRANSFORMERS_OFFLINE", "1")

if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))
