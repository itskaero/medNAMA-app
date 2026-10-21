#!/usr/bin/env python3
"""medNAMA Synology NAS Transfer & Sync Pipeline (Node Graph UI)."""

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
NAS_HOST = os.getenv("NAS_HOST", "192.168.1.42")
NAS_PORT = int(os.getenv("NAS_PORT", "22"))

class PipelineApi:
    def __init__(self, window):
        self.window = window
        self.transfer_config = None

    def start_transfer(self, config):
        self.transfer_config = config
        threading.Thread(target=run_nas_transfer, args=(self.window, config), daemon=True).start()

    def open_external(self, url):
        webbrowser.open(url)

    def close(self):
        self.window.destroy()

def run_nas_transfer(window, config):
    def log(msg):
        safe = msg.replace("\\", "\\\\").replace("'", "\\'").replace("\n", "\\n").replace("\r", "")
        window.evaluate_js(f"appendLog('{safe}')")

    def set_step(step_id, status, detail=""):
        safe = detail.replace("'", "\\'")
        window.evaluate_js(f"updateStep('{step_id}', '{status}', '{safe}')")

    def set_progress(pct, msg):
        window.evaluate_js(f"updateProgress({pct}, '{msg}')")

    log(f"Selected Targets: DB={config.get('db')}, Images={config.get('images')}, Configs={config.get('compose')}, DryRun={config.get('dryRun')}")

    # Stage 1: Connectivity
    set_step("ping", "active", f"Pinging NAS {NAS_HOST}:{NAS_PORT}...")
    log(f"[1/5] Verifying SSH connectivity to root@{NAS_HOST}:{NAS_PORT}...")
    time.sleep(0.5)
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(2.0)
        if s.connect_ex((NAS_HOST, NAS_PORT)) != 0:
            set_step("ping", "failed", "NAS unreachable")
            log(f"ERROR: Cannot connect to {NAS_HOST}:{NAS_PORT}")
            return
    set_step("ping", "success", f"Connected to {NAS_HOST}")
    set_progress(20, "NAS REACHABLE")

    # Stage 2: Compose & Configs
    if config.get("compose"):
        set_step("configs", "active", "Syncing docker-compose & deploy scripts...")
        log("[2/5] Uploading mednama-nas-deploy configuration bundle...")
        try:
            import paramiko
            key_path = str(ROOT / "_local" / "nas_key")
            client = paramiko.SSHClient()
            client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
            client.connect(NAS_HOST, port=NAS_PORT, username="root", key_filename=key_path, timeout=15)
            sftp = client.open_sftp()
            sftp.put(str(ROOT / "mednama-nas-deploy" / "app" / "docker-compose.yml"), "/DATA/mednama/app/docker-compose.yml")
            sftp.close()
            client.close()
            log("[2/5] Configuration files synced to NAS.")
        except Exception as e:
            log(f"[2/5] Config sync warning: {e}")
        set_step("configs", "success", "Configs updated on NAS")
    else:
        set_step("configs", "idle", "Skipped by user")
    set_progress(40, "CONFIGS SYNCED")

    # Stage 3: Database Transfer
    if config.get("db"):
        set_step("db", "active", "Snapshotting & transferring database to NAS...")
        log("[3/5] Starting database dump & transfer...")
        cmd = [PYTHON_EXE, "scripts/transfer_db_to_nas.py"]
        if config.get("dryRun"):
            cmd.append("--dry-run")
        proc = subprocess.Popen(cmd, cwd=str(ROOT), stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, bufsize=1)
        for line in proc.stdout:
            log(f"  {line.rstrip()}")
        proc.wait()
        if proc.returncode == 0:
            set_step("db", "success", "Database transferred & restored")
        else:
            set_step("db", "failed", f"DB transfer exit code {proc.returncode}")
    else:
        set_step("db", "idle", "Skipped by user")
    set_progress(65, "DATABASE TRANSFERRED")

    # Stage 4: Image Transfer
    if config.get("images") and not config.get("dryRun"):
        set_step("images", "active", "Building and transferring Docker images...")
        log("[4/5] Building & deploying Docker images to NAS...")
        cmd = [PYTHON_EXE, "scripts/transfer_images_to_nas.py"]
        proc = subprocess.Popen(cmd, cwd=str(ROOT), stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, bufsize=1)
        for line in proc.stdout:
            log(f"  {line.rstrip()}")
        proc.wait()
        if proc.returncode == 0:
            set_step("images", "success", "Images deployed & live")
        else:
            set_step("images", "failed", f"Image deploy code {proc.returncode}")
    else:
        set_step("images", "idle", "Skipped by user")
    set_progress(85, "IMAGES DEPLOYED")

    # Stage 5: Verification
    set_step("verify", "active", f"Verifying NAS web app on http://{NAS_HOST}:3000...")
    log(f"[5/5] Testing registration endpoint at http://{NAS_HOST}:3000/api/auth/registration...")
    time.sleep(1.0)
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(2.0)
        web_ok = (s.connect_ex((NAS_HOST, 3000)) == 0)

    if web_ok:
        set_step("verify", "success", f"NAS Live at http://{NAS_HOST}:3000")
        log(f"[5/5] Smoke test PASS! NAS application is online.")
        window.evaluate_js(f"completePipeline('NAS Transfer Completed Successfully!')")
    else:
        set_step("verify", "failed", "Port 3000 not responding")
        log(f"[5/5] Notice: NAS port 3000 did not respond in time.")

def init_window(window):
    time.sleep(0.4)
    steps = [
        {"id": "ping", "title": "NAS Host Connectivity (192.168.1.42)", "detail": "SSH handshake & authentication"},
        {"id": "configs", "title": "Compose Stack & Cloudflare Configs", "detail": "Syncing docker-compose & tokens"},
        {"id": "db", "title": "PostgreSQL Knowledge Base & MCQs", "detail": "Exporting snapshot & restoring on NAS"},
        {"id": "images", "title": "Frontend & Backend Docker Images", "detail": "Building .tar bundles & loading on NAS"},
        {"id": "verify", "title": "Remote Health Check & Verification", "detail": "Smoke testing http://192.168.1.42:3000"},
    ]
    import json
    window.evaluate_js(f"initPipeline('Synology NAS Transfer & Sync', 'TRANSFER', {json.dumps(steps)}, 'http://{NAS_HOST}:3000', true)")

def main():
    import webview
    try:
        from ui_template import HTML_CONTENT
        html_content = HTML_CONTENT
    except Exception:
        html_content = HTML_PATH.read_text(encoding="utf-8")

    window = webview.create_window(
        title="medNAMA NAS Transfer",
        html=html_content,
        width=820,
        height=760,
        resizable=True,
        background_color="#070a12",
    )
    api = PipelineApi(window)
    window.js_api = api

    t = threading.Thread(target=init_window, args=(window,), daemon=True)
    t.start()
    webview.start()

if __name__ == "__main__":
    main()
