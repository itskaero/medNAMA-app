@echo off
title medNAMA Transfer to NAS Pipeline
cd /d "%~dp0"
if exist "app-template\dist\Transfer-to-NAS\Transfer-to-NAS.exe" (
    start "" "app-template\dist\Transfer-to-NAS\Transfer-to-NAS.exe"
) else (
    echo [ERROR] Compiled executable not found, launching Python fallback...
    .venv-gpu\Scripts\python.exe app-template\transfer_to_nas.py
)

