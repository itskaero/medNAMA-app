#!/usr/bin/env python3
"""medNAMA Unified Application Launcher.

Manages all medNAMA services from one terminal or web dashboard:
  1. Database      (PostgreSQL :5434 via Docker)
  2. Main Backend  (FastAPI :8000)
  3. Main Frontend (Next.js :3000)
  4. Seed Studio   (TOS MCQ Studio :8770)
  5. RAG Lab       (Retrieval Evaluation :8765)
  6. Product Site  (Showcase & Landing :8080)
  7. NAS Sync      (Fast DB transfer to Synology NAS)

Usage:
  python launcher.py              # Interactive CLI + Web Dashboard (:8900)
  python launcher.py --web        # Web Dashboard only (auto-opens browser)
  python launcher.py --start-all  # Start all core services immediately
"""

from __future__ import annotations

import argparse
import http.server
import json
import os
import re
import socket
import subprocess
import sys
import threading
import time
import urllib.parse
import webbrowser
from dataclasses import dataclass, field
from pathlib import Path

ROOT = Path(__file__).resolve().parent
BACKEND_DIR = ROOT / "backend"
FRONTEND_DIR = ROOT / "frontend"
SITE_DIR = ROOT / "site"

# Auto-detect best python interpreter
def find_python() -> str:
    candidates = [
        ROOT / ".venv-gpu" / "Scripts" / "python.exe",
        ROOT / ".venv" / "Scripts" / "python.exe",
        ROOT / "venv" / "Scripts" / "python.exe",
        Path(sys.executable),
    ]
    for c in candidates:
        if c.exists():
            return str(c)
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


@dataclass
class Service:
    name: str
    display_name: str
    category: str  # "core", "lab", "site", "infra"
    port: int
    url: str
    description: str
    command: list[str]
    cwd: Path
    env: dict[str, str] = field(default_factory=dict)
    process: subprocess.Popen | None = None
    log_lines: list[str] = field(default_factory=list)
    lock: threading.Lock = field(default_factory=threading.Lock)

    def is_port_open(self) -> bool:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.settimeout(0.5)
            return s.connect_ex(("127.0.0.1", self.port)) == 0

    def get_status(self) -> str:
        """Returns: 'running' (by us), 'external' (port open elsewhere), 'stopped'"""
        if self.process is not None and self.process.poll() is None:
            return "running"
        if self.is_port_open():
            return "external"
        return "stopped"

    def start(self) -> tuple[bool, str]:
        if self.name == "db":
            # Docker container start
            try:
                res = subprocess.run(["docker", "start", "mednama-dev-db"], capture_output=True, text=True)
                if res.returncode == 0:
                    time.sleep(1)
                    return True, "Docker container mednama-dev-db started."
                return False, f"Docker start error: {res.stderr.strip()}"
            except Exception as e:
                return False, f"Failed to run docker: {e}"

        status = self.get_status()
        if status in ("running", "external"):
            return True, f"Already running on port {self.port}."

        try:
            full_env = os.environ.copy()
            full_env.update(self.env)
            full_env["PYTHONUNBUFFERED"] = "1"

            # On Windows, create a new process group for clean subtree termination
            creationflags = subprocess.CREATE_NEW_PROCESS_GROUP if sys.platform == "win32" else 0

            self.process = subprocess.Popen(
                self.command,
                cwd=str(self.cwd),
                env=full_env,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                bufsize=1,
                creationflags=creationflags,
            )

            # Start background reader for log lines
            threading.Thread(target=self._read_logs, daemon=True).start()

            # Wait briefly for process to bind or fail
            for _ in range(15):
                time.sleep(0.3)
                if self.process.poll() is not None:
                    return False, f"Process exited prematurely with code {self.process.returncode}."
                if self.is_port_open():
                    return True, f"Started on port {self.port}."

            return True, f"Started (PID {self.process.pid})."
        except Exception as e:
            return False, f"Start failed: {e}"

    def _read_logs(self):
        if not self.process or not self.process.stdout:
            return
        for line in self.process.stdout:
            with self.lock:
                self.log_lines.append(line.rstrip())
                if len(self.log_lines) > 200:
                    self.log_lines.pop(0)

    def stop(self) -> tuple[bool, str]:
        if self.name == "db":
            try:
                subprocess.run(["docker", "stop", "mednama-dev-db"], capture_output=True, text=True)
                return True, "Docker container stopped."
            except Exception as e:
                return False, f"Failed to stop docker: {e}"

        if self.process is None or self.process.poll() is not None:
            if self.is_port_open():
                return False, f"Port {self.port} is open by an external process. Close it manually."
            return True, "Service is already stopped."

        try:
            if sys.platform == "win32":
                # Windows subtree kill
                subprocess.run(["taskkill", "/F", "/T", "/PID", str(self.process.pid)], capture_output=True)
            else:
                self.process.terminate()
                try:
                    self.process.wait(timeout=3)
                except subprocess.TimeoutExpired:
                    self.process.kill()

            self.process = None
            return True, "Service stopped."
        except Exception as e:
            return False, f"Failed to stop: {e}"


class LauncherManager:
    def __init__(self):
        self.services: dict[str, Service] = {
            "db": Service(
                name="db",
                display_name="PostgreSQL Dev DB",
                category="infra",
                port=5434,
                url="",
                description="Local Docker PostgreSQL holding chunks, embeddings & MCQs",
                command=["docker", "start", "mednama-dev-db"],
                cwd=ROOT,
            ),
            "backend": Service(
                name="backend",
                display_name="FastAPI Main Backend",
                category="core",
                port=8000,
                url="http://localhost:8000/docs",
                description="Core REST API, RAG pipeline, Auth, Practice engine & AI endpoints",
                command=[PYTHON_EXE, "-m", "uvicorn", "app.main:app", "--host", "127.0.0.1", "--port", "8000"],
                cwd=BACKEND_DIR,
                env={"PYTHONPATH": str(BACKEND_DIR)},
            ),
            "frontend": Service(
                name="frontend",
                display_name="Next.js Main Frontend",
                category="core",
                port=3000,
                url="http://localhost:3000",
                description="Web application for medical students (MCQs, Mocks, RAG chat, Past papers)",
                command=[NPM_EXE, "run", "dev"],
                cwd=FRONTEND_DIR,
            ),
            "seed_studio": Service(
                name="seed_studio",
                display_name="TOS Seed Studio",
                category="lab",
                port=8770,
                url="http://localhost:8770",
                description="Interactive Table of Specifications MCQ authoring and bulk seed studio",
                command=[PYTHON_EXE, "-m", "labs.seed_studio.server"],
                cwd=ROOT,
            ),
            "rag_lab": Service(
                name="rag_lab",
                display_name="RAG Experiment Lab",
                category="lab",
                port=8765,
                url="http://localhost:8765",
                description="Multi-configuration RAG benchmarks, reranker comparisons & golden evaluations",
                command=[PYTHON_EXE, "-m", "labs.rag_lab.server"],
                cwd=ROOT,
            ),
            "site": Service(
                name="site",
                display_name="Product Showcase Site",
                category="site",
                port=8080,
                url="http://localhost:8080",
                description="Static public landing page, interactive showcase & feature tour",
                command=[PYTHON_EXE, "-m", "http.server", "8080", "--directory", "site"],
                cwd=ROOT,
            ),
        }
        self.nas_sync_log: list[str] = []
        self.nas_sync_busy = False

    def start_core(self) -> dict[str, str]:
        results = {}
        for key in ["db", "backend", "frontend"]:
            svc = self.services[key]
            ok, msg = svc.start()
            results[key] = msg
        return results

    def start_all(self) -> dict[str, str]:
        results = {}
        for key, svc in self.services.items():
            ok, msg = svc.start()
            results[key] = msg
        return results

    def stop_all(self) -> dict[str, str]:
        results = {}
        for key, svc in reversed(list(self.services.items())):
            if key == "db":
                continue  # Don't necessarily stop DB container unless requested
            ok, msg = svc.stop()
            results[key] = msg
        return results

    def trigger_nas_sync(self, dry_run=False):
        if self.nas_sync_busy:
            return False, "NAS Sync is already running."
        
        def run_thread():
            self.nas_sync_busy = True
            self.nas_sync_log = [f"[{time.strftime('%H:%M:%S')}] Starting NAS DB transfer..."]
            try:
                cmd = [PYTHON_EXE, "scripts/transfer_db_to_nas.py"]
                if dry_run:
                    cmd.append("--dry-run")
                proc = subprocess.Popen(
                    cmd,
                    cwd=str(ROOT),
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,
                    text=True,
                    bufsize=1,
                )
                for line in proc.stdout:
                    self.nas_sync_log.append(line.rstrip())
                    if len(self.nas_sync_log) > 500:
                        self.nas_sync_log.pop(0)
                proc.wait()
                self.nas_sync_log.append(f"[{time.strftime('%H:%M:%S')}] Transfer finished with code {proc.returncode}.")
            except Exception as e:
                self.nas_sync_log.append(f"Error running transfer: {e}")
            finally:
                self.nas_sync_busy = False

        threading.Thread(target=run_thread, daemon=True).start()
        return True, "NAS Sync started in background."

    def trigger_nas_image_deploy(self):
        if self.nas_sync_busy:
            return False, "Another NAS operation is already running."

        def run_thread():
            self.nas_sync_busy = True
            self.nas_sync_log = [f"[{time.strftime('%H:%M:%S')}] Starting NAS Docker Image build & deployment..."]
            try:
                cmd = [PYTHON_EXE, "scripts/transfer_images_to_nas.py"]
                proc = subprocess.Popen(
                    cmd,
                    cwd=str(ROOT),
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,
                    text=True,
                    bufsize=1,
                )
                for line in proc.stdout:
                    self.nas_sync_log.append(line.rstrip())
                    if len(self.nas_sync_log) > 500:
                        self.nas_sync_log.pop(0)
                proc.wait()
                self.nas_sync_log.append(f"[{time.strftime('%H:%M:%S')}] Image deployment finished with code {proc.returncode}.")
            except Exception as e:
                self.nas_sync_log.append(f"Error deploying images: {e}")
            finally:
                self.nas_sync_busy = False

        threading.Thread(target=run_thread, daemon=True).start()
        return True, "NAS Image deployment started in background."

    def get_nas_telemetry(self) -> dict:
        host = "192.168.1.42"
        ssh_ok = False
        web_ok = False
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
                s.settimeout(0.6)
                ssh_ok = (s.connect_ex((host, 22)) == 0)
        except Exception:
            pass
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
                s.settimeout(0.6)
                web_ok = (s.connect_ex((host, 3000)) == 0)
        except Exception:
            pass

        return {
            "host": host,
            "ssh_port_open": ssh_ok,
            "web_port_open": web_ok,
            "web_url": f"http://{host}:3000",
            "active_db": "medrag_20261012_220436",
            "pget_count": 792,
            "total_mcqs": 76232,
        }

    def get_all_logs(self) -> dict[str, list[str]]:
        logs = {}
        for key, svc in self.services.items():
            with svc.lock:
                logs[key] = list(svc.log_lines[-60:])
        logs["nas"] = list(self.nas_sync_log[-60:])
        return logs

    def get_summary(self) -> list[dict]:
        out = []
        for key, svc in self.services.items():
            st = svc.get_status()
            out.append({
                "id": key,
                "name": svc.display_name,
                "category": svc.category,
                "port": svc.port,
                "url": svc.url,
                "status": st,
                "pid": svc.process.pid if svc.process and svc.process.poll() is None else None,
                "description": svc.description,
            })
        return out


MANAGER = LauncherManager()

# -----------------------------------------------------------------------------
# Embedded Web Dashboard
# -----------------------------------------------------------------------------
DASHBOARD_HTML = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>medNAMA · Control Hub</title>
<style>
  :root {
    --bg: #090d16;
    --surface: #121826;
    --surface-hover: #182235;
    --border: #232f45;
    --primary: #0ea5e9;
    --primary-glow: rgba(14, 165, 233, 0.2);
    --emerald: #10b981;
    --emerald-dim: rgba(16, 185, 129, 0.15);
    --amber: #f59e0b;
    --amber-dim: rgba(245, 158, 11, 0.15);
    --rose: #ef4444;
    --rose-dim: rgba(239, 68, 68, 0.15);
    --text: #f1f5f9;
    --muted: #94a3b8;
  }
  * { box-sizing: border-box; margin: 0; padding: 0; font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif; }
  body { background: var(--bg); color: var(--text); min-height: 100vh; padding: 24px; }
  .container { max-width: 1100px; margin: 0 auto; }
  header { display: flex; justify-content: space-between; align-items: center; margin-bottom: 24px; padding-bottom: 16px; border-bottom: 1px solid var(--border); }
  .logo-area { display: flex; align-items: center; gap: 12px; }
  .logo-badge { background: linear-gradient(135deg, #0ea5e9, #3b82f6); color: white; font-weight: 800; font-size: 1.1rem; padding: 6px 12px; border-radius: 8px; letter-spacing: 0.5px; }
  h1 { font-size: 1.4rem; font-weight: 700; }
  .subtitle { color: var(--muted); font-size: 0.85rem; margin-top: 2px; }
  .actions-top { display: flex; gap: 10px; }
  button { cursor: pointer; border: 1px solid var(--border); border-radius: 8px; padding: 8px 14px; font-size: 0.82rem; font-weight: 600; background: var(--surface); color: var(--text); transition: all 0.15s; }
  button:hover { background: var(--surface-hover); border-color: var(--primary); }
  button:disabled { opacity: 0.5; cursor: not-allowed; }
  .btn-primary { background: #0284c7; border-color: #0369a1; color: white; }
  .btn-primary:hover { background: #0369a1; }
  .btn-emerald { background: #059669; border-color: #047857; color: white; }
  .btn-emerald:hover { background: #047857; }
  .btn-rose { background: #dc2626; border-color: #b91c1c; color: white; }
  .btn-rose:hover { background: #b91c1c; }
  .grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(320px, 1fr)); gap: 16px; margin-bottom: 24px; }
  .card { background: var(--surface); border: 1px solid var(--border); border-radius: 12px; padding: 18px; display: flex; flex-direction: column; justify-content: space-between; position: relative; overflow: hidden; transition: transform 0.15s, border-color 0.15s; }
  .card:hover { border-color: #334155; transform: translateY(-2px); }
  .card-top { display: flex; justify-content: space-between; align-items: flex-start; margin-bottom: 8px; }
  .card-title { font-size: 1.05rem; font-weight: 600; display: flex; align-items: center; gap: 8px; }
  .card-desc { color: var(--muted); font-size: 0.8rem; line-height: 1.4; margin-bottom: 16px; flex-grow: 1; }
  .badge { display: inline-flex; align-items: center; gap: 6px; font-size: 0.72rem; font-weight: 700; text-transform: uppercase; padding: 3px 8px; border-radius: 9999px; }
  .badge-dot { width: 7px; height: 7px; border-radius: 50%; }
  .badge-running { background: var(--emerald-dim); color: var(--emerald); border: 1px solid var(--emerald); }
  .badge-running .badge-dot { background: var(--emerald); box-shadow: 0 0 6px var(--emerald); }
  .badge-external { background: var(--amber-dim); color: var(--amber); border: 1px solid var(--amber); }
  .badge-external .badge-dot { background: var(--amber); }
  .badge-stopped { background: rgba(148, 163, 184, 0.1); color: var(--muted); border: 1px solid var(--border); }
  .badge-stopped .badge-dot { background: var(--muted); }
  .card-bottom { display: flex; justify-content: space-between; align-items: center; padding-top: 12px; border-top: 1px solid var(--border); }
  .port-tag { font-family: monospace; font-size: 0.78rem; color: var(--muted); background: rgba(0,0,0,0.3); padding: 2px 6px; border-radius: 4px; }
  .card-buttons { display: flex; gap: 6px; }
  .section-title { font-size: 0.95rem; font-weight: 700; color: var(--muted); text-transform: uppercase; letter-spacing: 0.8px; margin: 24px 0 12px 0; }
  .nas-panel { background: var(--surface); border: 1px solid var(--border); border-radius: 12px; padding: 18px; margin-bottom: 24px; }
  .nas-head { display: flex; justify-content: space-between; align-items: center; margin-bottom: 12px; }
  .log-box { background: #05070d; border: 1px solid #1a2233; border-radius: 8px; padding: 12px; font-family: monospace; font-size: 0.75rem; color: #a5b4fc; max-height: 180px; overflow-y: auto; white-space: pre-wrap; line-height: 1.4; }
</style>
</head>
<body>
<div class="container">
  <header>
    <div class="logo-area">
      <div class="logo-badge">medNAMA</div>
      <div>
        <h1>Unified Application Hub</h1>
        <div class="subtitle">Main Apps · Labs · Sub-apps · Database & NAS Sync</div>
      </div>
    </div>
    <div class="actions-top">
      <button class="btn-emerald" onclick="startCore()">Start Core Apps</button>
      <button class="btn-primary" onclick="startAll()">Start Everything</button>
      <button class="btn-rose" onclick="stopAll()">Stop All</button>
    </div>
  </header>

  <div class="section-title">Active Services</div>
  <div class="grid" id="servicesGrid">Loading services...</div>

  <div class="section-title">NAS Database Transfer & Backup</div>
  <div class="nas-panel">
    <div class="nas-head">
      <div>
        <strong>Transfer Local Seeded Database to Synology NAS (192.168.1.42)</strong>
        <div class="subtitle">Compresses database, uploads to NAS, restores into new medrag DB, merges live user attempts, and cuts over backend.</div>
      </div>
      <div style="display: flex; gap: 8px;">
        <button onclick="syncNas(true)">Dry Run Check</button>
        <button class="btn-emerald" onclick="syncNas(false)">Start Full Transfer</button>
      </div>
    </div>
    <div class="log-box" id="nasLogs">No transfer activity yet.</div>
  </div>
</div>

<script>
async function loadStatus() {
  try {
    const res = await fetch('/api/status');
    const data = await res.json();
    renderGrid(data.services);
    if (data.nas_log && data.nas_log.length) {
      document.getElementById('nasLogs').textContent = data.nas_log.join('\\n');
    }
  } catch (e) {
    console.error(e);
  }
}

function renderGrid(services) {
  const grid = document.getElementById('servicesGrid');
  grid.innerHTML = services.map(s => {
    const isRun = s.status === 'running';
    const isExt = s.status === 'external';
    const badgeClass = isRun ? 'badge-running' : (isExt ? 'badge-external' : 'badge-stopped');
    const badgeLabel = isRun ? 'Online' : (isExt ? 'External' : 'Stopped');
    
    return `
      <div class="card">
        <div>
          <div class="card-top">
            <div class="card-title">${s.name}</div>
            <span class="badge ${badgeClass}"><span class="badge-dot"></span>${badgeLabel}</span>
          </div>
          <div class="card-desc">${s.description}</div>
        </div>
        <div class="card-bottom">
          <span class="port-tag">:${s.port}</span>
          <div class="card-buttons">
            ${s.url ? `<a href="${s.url}" target="_blank" style="text-decoration:none;"><button type="button" ${(!isRun && !isExt) ? 'disabled' : ''}>Open</button></a>` : ''}
            ${(isRun || isExt) 
              ? `<button class="btn-rose" onclick="stopService('${s.id}')">Stop</button>` 
              : `<button class="btn-emerald" onclick="startService('${s.id}')">Start</button>`}
          </div>
        </div>
      </div>
    `;
  }).join('');
}

async function startService(id) { await fetch(`/api/service/start?id=${id}`, {method: 'POST'}); loadStatus(); }
async function stopService(id) { await fetch(`/api/service/stop?id=${id}`, {method: 'POST'}); loadStatus(); }
async function startCore() { await fetch('/api/start-core', {method: 'POST'}); loadStatus(); }
async function startAll() { await fetch('/api/start-all', {method: 'POST'}); loadStatus(); }
async function stopAll() { await fetch('/api/stop-all', {method: 'POST'}); loadStatus(); }
async function syncNas(dryRun) { await fetch(`/api/nas-sync?dry_run=${dryRun}`, {method: 'POST'}); loadStatus(); }

loadStatus();
setInterval(loadStatus, 2500);
</script>
</body>
</html>
"""


class DashboardHandler(http.server.BaseHTTPRequestHandler):
    def log_message(self, format, *args):
        pass  # Quiet logging

    def do_GET(self):
        url = urllib.parse.urlparse(self.path)
        if url.path == "/" or url.path == "/index.html":
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.end_headers()
            self.wfile.write(DASHBOARD_HTML.encode("utf-8"))
        elif url.path == "/api/status":
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            payload = {
                "services": MANAGER.get_summary(),
                "nas_log": MANAGER.nas_sync_log,
                "nas_busy": MANAGER.nas_sync_busy,
            }
            self.wfile.write(json.dumps(payload).encode("utf-8"))
        elif url.path == "/api/nas-status":
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            payload = MANAGER.get_nas_telemetry()
            self.wfile.write(json.dumps(payload).encode("utf-8"))
        elif url.path == "/api/logs":
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            payload = MANAGER.get_all_logs()
            self.wfile.write(json.dumps(payload).encode("utf-8"))
        else:
            self.send_error(404)

    def do_POST(self):
        url = urllib.parse.urlparse(self.path)
        params = urllib.parse.parse_qs(url.query)
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.end_headers()

        res = {"ok": True}
        if url.path == "/api/service/start":
            svc_id = params.get("id", [""])[0]
            if svc_id in MANAGER.services:
                ok, msg = MANAGER.services[svc_id].start()
                res = {"ok": ok, "message": msg}
        elif url.path == "/api/service/stop":
            svc_id = params.get("id", [""])[0]
            if svc_id in MANAGER.services:
                ok, msg = MANAGER.services[svc_id].stop()
                res = {"ok": ok, "message": msg}
        elif url.path == "/api/start-core":
            res = {"results": MANAGER.start_core()}
        elif url.path == "/api/start-all":
            res = {"results": MANAGER.start_all()}
        elif url.path == "/api/stop-all":
            res = {"results": MANAGER.stop_all()}
        elif url.path == "/api/nas-sync":
            dry = params.get("dry_run", ["false"])[0].lower() == "true"
            ok, msg = MANAGER.trigger_nas_sync(dry_run=dry)
            res = {"ok": ok, "message": msg}
        elif url.path == "/api/nas-deploy-images":
            ok, msg = MANAGER.trigger_nas_image_deploy()
            res = {"ok": ok, "message": msg}

        self.wfile.write(json.dumps(res).encode("utf-8"))


def run_web_server(port=8900):
    server = http.server.ThreadingHTTPServer(("127.0.0.1", port), DashboardHandler)
    server.serve_forever()


# -----------------------------------------------------------------------------
# Terminal CLI Dashboard
# -----------------------------------------------------------------------------
def print_cli_dashboard():
    os.system("cls" if sys.platform == "win32" else "clear")
    print("=" * 72)
    print("               medNAMA Unified Control Hub")
    print("=" * 72)
    print(f" {'#':<3} {'Service':<24} {'Port':<7} {'Status':<12} {'URL'}")
    print("-" * 72)

    keys = list(MANAGER.services.keys())
    for idx, key in enumerate(keys, 1):
        svc = MANAGER.services[key]
        st = svc.get_status()
        st_label = "[ONLINE]" if st == "running" else ("[EXTERNAL]" if st == "external" else "[STOPPED]")
        url_label = svc.url if svc.url else "-"
        print(f" [{idx}] {svc.display_name:<24} :{svc.port:<5} {st_label:<12} {url_label}")

    print("-" * 72)
    print(" [C] Start Core Apps (DB + Backend + Frontend)")
    print(" [A] Start All Services (Core + Labs + Site)")
    print(" [X] Stop All Running Services")
    print(" [N] Sync Database to NAS (Paramiko SSH/SFTP)")
    print(" [W] Open Web Dashboard (http://127.0.0.1:8900)")
    print(" [O] Open Running Apps in Browser")
    print(" [Q] Quit Launcher")
    print("=" * 72)


def interactive_cli():
    keys = list(MANAGER.services.keys())
    while True:
        print_cli_dashboard()
        try:
            choice = input("\nEnter selection: ").strip().lower()
        except (KeyboardInterrupt, EOFError):
            break

        if choice == "q":
            print("\nStopping services launched by manager...")
            MANAGER.stop_all()
            print("Goodbye.")
            break
        elif choice == "c":
            print("\nStarting Core apps...")
            MANAGER.start_core()
            time.sleep(1.5)
        elif choice == "a":
            print("\nStarting All services...")
            MANAGER.start_all()
            time.sleep(1.5)
        elif choice == "x":
            print("\nStopping services...")
            MANAGER.stop_all()
            time.sleep(1)
        elif choice == "w":
            webbrowser.open("http://127.0.0.1:8900")
        elif choice == "o":
            for svc in MANAGER.services.values():
                if svc.url and svc.get_status() in ("running", "external"):
                    webbrowser.open(svc.url)
        elif choice == "n":
            print("\nTriggering NAS DB Sync...")
            ok, msg = MANAGER.trigger_nas_sync()
            print(msg)
            time.sleep(2)
        elif choice.isdigit():
            num = int(choice)
            if 1 <= num <= len(keys):
                svc = MANAGER.services[keys[num - 1]]
                if svc.get_status() in ("running", "external"):
                    print(f"\nStopping {svc.display_name}...")
                    svc.stop()
                else:
                    print(f"\nStarting {svc.display_name}...")
                    svc.start()
                time.sleep(1)


def main():
    parser = argparse.ArgumentParser(description="medNAMA Unified Launcher")
    parser.add_argument("--web", action="store_true", help="Run web dashboard only and open browser")
    parser.add_argument("--start-all", action="store_true", help="Start all services immediately")
    parser.add_argument("--start-core", action="store_true", help="Start core services (DB, backend, frontend)")
    parser.add_argument("--port", type=int, default=8900, help="Web dashboard port (default: 8900)")
    args = parser.parse_args()

    # Start web server in background thread
    web_thread = threading.Thread(target=run_web_server, args=(args.port,), daemon=True)
    web_thread.start()

    if args.start_all:
        MANAGER.start_all()
    elif args.start_core:
        MANAGER.start_core()

    if args.web:
        print(f"medNAMA Control Hub running at http://127.0.0.1:{args.port}")
        webbrowser.open(f"http://127.0.0.1:{args.port}")
        try:
            while True:
                time.sleep(1)
        except KeyboardInterrupt:
            MANAGER.stop_all()
    else:
        # Open web dashboard in background and run CLI
        interactive_cli()


if __name__ == "__main__":
    main()

