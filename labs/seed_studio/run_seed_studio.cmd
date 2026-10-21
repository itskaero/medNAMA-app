@echo off
rem Seed Studio: double-click, or run from any terminal. Opens http://127.0.0.1:8770 when ready.
rem Needs Docker Desktop running (the dev database on port 5434) and the root .env (AI keys).
cd /d "%~dp0..\.."
docker info >nul 2>&1 || (echo Starting Docker Desktop... & start "" "C:\Program Files\Docker\Docker\Docker Desktop.exe" & timeout /t 40 >nul)
docker start mednama-dev-db >nul 2>&1
start "" http://127.0.0.1:8770
".venv-gpu\Scripts\python.exe" -m labs.seed_studio.server
pause
