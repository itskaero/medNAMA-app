"""Lab settings (all overridable by environment variables) and the read-only bridge to the backend.

Import this module first: it decides the compute device before torch is imported anywhere.

    RAG_LAB_DEVICE         cpu (default, like production: the NAS has no GPU) | cuda
    RAG_LAB_PORT           local UI port (default 8765)
    RAG_LAB_MODEL_MSMARCO  stage-1 cross-encoder        (default production's)
    RAG_LAB_MODEL_MEDCPT   biomedical second stage      (default production's)
    RAG_LAB_MODEL_BGE_BASE BGE base reranker
    RAG_LAB_MODEL_BGE_M3   BGE v2-m3 reranker
    RAG_LAB_ANALYSIS_ROLE  LLM role for question understanding / coverage / planning / judge: chat (default) | fast
    RAG_LAB_DATA           where runs and evaluations are stored (default rag_lab/data)

The LLM itself is production's (backend/app/llm.py, settings from the root .env): the lab never
picks a different generator, so every configuration is answered by the same model.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

LAB_DIR = Path(__file__).resolve().parent
REPO = LAB_DIR.parent.parent          # labs/rag_lab -> repo root
BACKEND = REPO / "backend"
DATA = Path(os.environ.get("RAG_LAB_DATA") or LAB_DIR / "data")
RUNS = DATA / "runs"
HUMAN_EVALS = DATA / "evaluations_human"
AI_EVALS = DATA / "evaluations_ai"
QUESTIONS = LAB_DIR / "questions.json"
LAB_VERSION = "0.1.0"

DEVICE = os.environ.get("RAG_LAB_DEVICE", "cpu").strip().lower()
if DEVICE == "cpu":
    # Hide the GPU before torch loads, so the embedding model also runs as production runs it
    # (CPU, INT8) and timings/RAM reflect a GPU-less server.
    os.environ.setdefault("CUDA_VISIBLE_DEVICES", "")
os.environ.setdefault("HF_HUB_OFFLINE", "1")          # use the local model cache; never fetch mid-run
os.environ.setdefault("TRANSFORMERS_OFFLINE", "1")

MODELS = {
    "msmarco": os.environ.get("RAG_LAB_MODEL_MSMARCO", "cross-encoder/ms-marco-MiniLM-L-6-v2"),
    "medcpt": os.environ.get("RAG_LAB_MODEL_MEDCPT", "ncbi/MedCPT-Cross-Encoder"),
    "bge_base": os.environ.get("RAG_LAB_MODEL_BGE_BASE", "BAAI/bge-reranker-base"),
    "bge_v2_m3": os.environ.get("RAG_LAB_MODEL_BGE_M3", "BAAI/bge-reranker-v2-m3"),
}
ANALYSIS_ROLE = os.environ.get("RAG_LAB_ANALYSIS_ROLE", "chat")
PORT = int(os.environ.get("RAG_LAB_PORT", "8765"))
# 127.0.0.1 = this PC only (default). 0.0.0.0 = also phones/PCs on your network. The lab has no login and
# every run spends LLM tokens, so only open it on a network you trust.
HOST = os.environ.get("RAG_LAB_HOST", "127.0.0.1")

LEVELS = ("MBBS", "FCPS-I", "FCPS-II")
STRICTNESS = ("strict", "tutor")


def use_backend() -> None:
    """Make `import app...` resolve to the production backend. Read-only use: the lab imports its
    functions and constants and never writes to its modules' state outside its own process."""
    if str(BACKEND) not in sys.path:
        sys.path.insert(0, str(BACKEND))


def git_state() -> dict[str, object]:
    def run(*args: str) -> str:
        try:
            return subprocess.run(["git", *args], cwd=REPO, capture_output=True, text=True, timeout=10).stdout.strip()
        except Exception:
            return ""
    return {"commit": run("rev-parse", "HEAD") or None, "branch": run("rev-parse", "--abbrev-ref", "HEAD") or None,
            "dirty": bool(run("status", "--porcelain", "--untracked-files=no"))}


use_backend()
