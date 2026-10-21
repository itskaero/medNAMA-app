@echo off
rem PaedNAMA, with medNAMA alongside on ONE backend:
rem   server  :8780  medNAMA's API + /api/paeds (the search models load once)
rem   web     :3100  PaedNAMA
rem   web     :3000  medNAMA, pointed at the same server (so "Open medNAMA" works)
rem Needs Docker running (the dev database mednama-dev-db on port 5434). Close the three windows to stop.
rem Don't run this while books are being ingested: it takes the memory the ingestion needs.
cd /d "%~dp0"

docker start mednama-dev-db >nul 2>&1
if errorlevel 1 (
  echo Docker is not running. Start Docker Desktop, wait for it to say "running", then run this again.
  pause
  exit /b 1
)

if not exist "apps\paednama\web\node_modules" call "apps\paednama\scripts\link_modules.bat"

start "PaedNAMA server" cmd /k ".venv-gpu\Scripts\python.exe -m apps.paednama.server.main"
start "PaedNAMA web" cmd /k "cd /d apps\paednama\web && npm run dev"
start "medNAMA web (on the PaedNAMA server)" cmd /k "cd /d frontend && set BACKEND_URL=http://127.0.0.1:8780&& npm run dev"

echo Starting PaedNAMA and medNAMA... the first start takes about a minute (search models, then the web builds).
timeout /t 45 /nobreak >nul
start "" http://localhost:3100
