@echo off
title Launch medNAMA Core Stack
cd /d "%~dp0"
if exist "app-template\dist\Launch-medNAMA\Launch-medNAMA.exe" (
    start "" "app-template\dist\Launch-medNAMA\Launch-medNAMA.exe"
) else (
    echo [ERROR] Compiled executable not found, launching Python fallback...
    .venv-gpu\Scripts\python.exe app-template\launch_mednama.py
)

