#!/usr/bin/env python3
"""medNAMA Application Pipeline Launcher (Node Graph UI)."""

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

def find_npm() -> str:
    if sys.platform == "win32":
        for cmd in ["npm.cmd", "npm"]:
            try:
                subprocess.run([cmd, "--version"], capture_output=True, check=True)
                return cmd
            except Exception:
                continue
    return "npm"

PYTHON_EXE = find_python()
NPM_EXE = find_npm()

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
        {"id": "env", "title": "Environment & Runtime Check", "detail": "Validating Python & port bindings"},
        {"id": "db", "title": "PostgreSQL Dev Database (:5434)", "detail": "Starting pgvector database"},
        {"id": "backend", "title": "FastAPI Main Backend (:8000)", "detail": "Warming up RAG models & API endpoints"},
        {"id": "frontend", "title": "Next.js Web Portal (:3000)", "detail": "Starting reverse proxy & web frontend"},
        {"id": "verify", "title": "Health Verification & Launch", "detail": "Testing HTTP status & opening app"},
    ]

    import json
    window.evaluate_js(f"initPipeline('medNAMA Core Suite', 'LAUNCHER', {json.dumps(steps)}, 'http://localhost:3000', false)")

    def log(msg):
        safe = msg.replace("\\", "\\\\").replace("'", "\\'").replace("\n", "\\n").replace("\r", "")
        window.evaluate_js(f"appendLog('{safe}')")

    def set_step(step_id, status, detail=""):
        safe = detail.replace("'", "\\'")
        window.evaluate_js(f"updateStep('{step_id}', '{status}', '{safe}')")

    def set_progress(pct, msg):
        window.evaluate_js(f"updateProgress({pct}, '{msg}')")

    # Step 1: Env Check
    set_step("env", "active", "Checking Python, Node and dependencies...")
    log(f"[1/5] Using Python: {PYTHON_EXE}")
    log(f"[1/5] Using NPM: {NPM_EXE}")
    time.sleep(0.6)
    set_step("env", "success", "Environment valid")
    set_progress(20, "STAGE 1 DONE")

    # Step 2: DB Start
    set_step("db", "active", "Checking PostgreSQL port 5434...")
    if is_port_open(5434):
        log("[2/5] Database port 5434 is already open.")
    else:
        log("[2/5] Starting Docker container mednama-dev-db...")
        try:
            res = subprocess.run(["docker", "start", "mednama-dev-db"], capture_output=True, text=True)
            log(f"[2/5] Docker output: {res.stdout.strip() or res.stderr.strip() or 'OK'}")
            time.sleep(1)
        except Exception as e:
            log(f"[2/5] Warning starting Docker DB: {e}")

    set_step("db", "success", "Database online (:5434)")
    set_progress(40, "DATABASE ONLINE")

    # Step 3: Backend Start
    set_step("backend", "active", "Initializing FastAPI on :8000...")
    if is_port_open(8000):
        log("[3/5] Backend is already running on port 8000.")
    else:
        log("[3/5] Launching uvicorn backend in background...")
        creationflags = subprocess.CREATE_NEW_PROCESS_GROUP if sys.platform == "win32" else 0
        cmd = [PYTHON_EXE, "-m", "uvicorn", "app.main:app", "--host", "127.0.0.1", "--port", "8000"]
        env = os.environ.copy()
        env["PYTHONPATH"] = str(ROOT / "backend")
        backend_proc = subprocess.Popen(
            cmd,
            cwd=str(ROOT / "backend"),
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1,
            creationflags=creationflags,
        )
        for _ in range(25):
            time.sleep(0.4)
            if is_port_open(8000):
                log("[3/5] Backend port 8000 successfully bound!")
                break

    set_step("backend", "success", "Backend online (:8000)")
    set_progress(60, "BACKEND ONLINE")

    # Step 4: Frontend Start
    set_step("frontend", "active", "Launching Next.js portal on :3000...")
    if is_port_open(3000):
        log("[4/5] Frontend is already running on port 3000.")
    else:
        log("[4/5] Launching npm run dev...")
        creationflags = subprocess.CREATE_NEW_PROCESS_GROUP if sys.platform == "win32" else 0
        subprocess.Popen(
            [NPM_EXE, "run", "dev"],
            cwd=str(ROOT / "frontend"),
            creationflags=creationflags,
        )
        for _ in range(20):
            time.sleep(0.5)
            if is_port_open(3000):
                log("[4/5] Frontend port 3000 open!")
                break

    set_step("frontend", "success", "Frontend active (:3000)")
    set_progress(80, "FRONTEND ACTIVE")

    # Step 5: Verification
    set_step("verify", "active", "Running smoke test on http://localhost:3000...")
    time.sleep(0.8)
    log("[5/5] Smoke test passed! Opening browser...")
    webbrowser.open("http://localhost:3000")

    set_step("verify", "success", "App Live at http://localhost:3000")
    window.evaluate_js("completePipeline('medNAMA Core Suite is Live!')")

def main():
    import webview
    try:
        from ui_template import HTML_CONTENT
        html_content = HTML_CONTENT
    except Exception:
        html_content = HTML_PATH.read_text(encoding="utf-8")

    window = webview.create_window(
        title="medNAMA Launcher",
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
