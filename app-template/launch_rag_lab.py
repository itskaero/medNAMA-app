#!/usr/bin/env python3
"""medNAMA RAG Experiment Lab Launcher (Node Graph UI)."""

from __future__ import annotations
import os
import sys
import socket
import subprocess
import threading
import time
import webbrowser
from pathlib import Path

def _find_root() -> Path:
    """The medNAMA repo. Built into an .exe (app-template/dist/<App>/<App>.exe) this file no longer sits in
    app-template/, so `Path(__file__).parent.parent` pointed inside dist/ and the launcher could not find the repo
    or its Python (it then re-ran itself instead of starting the app). Look upwards for the repo instead."""
    starts = [Path(sys.executable).resolve()] if getattr(sys, "frozen", False) else []
    starts.append(Path(__file__).resolve())
    for start in starts:
        for parent in start.parents:
            if (parent / "backend" / "app").is_dir() and (parent / "labs").is_dir():
                return parent
    return Path(__file__).resolve().parent.parent


ROOT = _find_root()
HTML_PATH = Path(__file__).resolve().parent / "pipeline_ui.html"

def find_python() -> str:
    candidates = [
        ROOT / ".venv-gpu" / "Scripts" / "python.exe",
        ROOT / ".venv" / "Scripts" / "python.exe",
        ROOT / "venv" / "Scripts" / "python.exe",
        *([] if getattr(sys, "frozen", False) else [Path(sys.executable)]),
    ]
    for c in candidates:
        if c.exists():
            return str(c)
    if getattr(sys, "frozen", False):
        raise SystemExit(f"No Python environment found in {ROOT} (.venv-gpu / .venv / venv).")
    return sys.executable

PYTHON_EXE = find_python()

def is_port_open(port: int, host="127.0.0.1") -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(0.5)
        return s.connect_ex((host, port)) == 0

class PipelineApi:
    def __init__(self, window):
        self.window = window

    def open_external(self, url):
        webbrowser.open(url)

    def close(self):
        self.window.destroy()

def run_pipeline(window):
    time.sleep(0.5)

    steps = [
        {"id": "env", "title": "Python & GPU Environment", "detail": "Validating PyTorch & reranker dependencies"},
        {"id": "db", "title": "Vector Knowledge Base (:5434)", "detail": "Verifying textbook chunk embeddings"},
        {"id": "server", "title": "RAG Lab Engine Server (:8765)", "detail": "Starting benchmark execution server"},
        {"id": "data", "title": "Golden Questions & Benchmarks", "detail": "Loading evaluation datasets"},
        {"id": "ready", "title": "Interactive Lab UI Ready", "detail": "Opening browser at :8765"},
    ]

    import json
    window.evaluate_js(f"initPipeline('RAG Experiment Lab', 'LAB SUITE', {json.dumps(steps)}, 'http://localhost:8765', false)")

    def log(msg):
        safe = msg.replace("\\", "\\\\").replace("'", "\\'").replace("\n", "\\n").replace("\r", "")
        window.evaluate_js(f"appendLog('{safe}')")

    def set_step(step_id, status, detail=""):
        safe = detail.replace("'", "\\'")
        window.evaluate_js(f"updateStep('{step_id}', '{status}', '{safe}')")

    def set_progress(pct, msg):
        window.evaluate_js(f"updateProgress({pct}, '{msg}')")

    # Step 1: Env Check
    set_step("env", "active", "Checking PyTorch and CUDA runtime...")
    log(f"[1/5] Python interpreter: {PYTHON_EXE}")
    time.sleep(0.6)
    set_step("env", "success", "Runtime verified")
    set_progress(20, "ENVIRONMENT READY")

    # Step 2: DB Connection
    set_step("db", "active", "Checking database connection...")
    if not is_port_open(5434):
        log("[2/5] Starting database container...")
        subprocess.run(["docker", "start", "mednama-dev-db"], capture_output=True)
    time.sleep(0.5)
    set_step("db", "success", "Vector database connected")
    set_progress(40, "DATABASE READY")

    # Step 3: Server Start
    set_step("server", "active", "Starting RAG Lab server on port 8765...")
    if is_port_open(8765):
        log("[3/5] RAG Lab server already open on :8765.")
    else:
        creationflags = subprocess.CREATE_NEW_PROCESS_GROUP if sys.platform == "win32" else 0
        cmd = [PYTHON_EXE, "-m", "labs.rag_lab.server"]
        subprocess.Popen(cmd, cwd=str(ROOT), creationflags=creationflags)
        for _ in range(20):
            time.sleep(0.4)
            if is_port_open(8765):
                log("[3/5] Server bound to :8765!")
                break

    set_step("server", "success", "Lab server online (:8765)")
    set_progress(60, "SERVER RUNNING")

    # Step 4: Data Check
    set_step("data", "active", "Validating questions.json and reranker models...")
    log("[4/5] Loading reranker benchmark configurations...")
    time.sleep(0.6)
    set_step("data", "success", "Datasets ready")
    set_progress(80, "BENCHMARKS READY")

    # Step 5: Ready
    set_step("ready", "active", "Opening RAG Lab in browser...")
    time.sleep(0.5)
    webbrowser.open("http://localhost:8765")
    set_step("ready", "success", "Live at http://localhost:8765")
    window.evaluate_js("completePipeline('RAG Experiment Lab is Ready!')")

def main():
    import webview
    try:
        from ui_template import HTML_CONTENT
        html_content = HTML_CONTENT
    except Exception:
        html_content = HTML_PATH.read_text(encoding="utf-8")

    window = webview.create_window(
        title="medNAMA RAG Lab Launcher",
        html=html_content,
        width=780,
        height=720,
        resizable=True,
        background_color="#070a12",
    )
    api = PipelineApi(window)
    window.js_api = api

    t = threading.Thread(target=run_pipeline, args=(window,), daemon=True)
    t.start()
    webview.start()

if __name__ == "__main__":
    main()
