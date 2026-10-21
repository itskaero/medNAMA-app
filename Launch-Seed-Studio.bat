@echo off
title Launch medNAMA TOS Seed Studio
cd /d "%~dp0"
if exist "app-template\dist\Launch-Seed-Studio\Launch-Seed-Studio.exe" (
    start "" "app-template\dist\Launch-Seed-Studio\Launch-Seed-Studio.exe"
) else (
    echo [ERROR] Compiled executable not found, launching Python fallback...
    .venv-gpu\Scripts\python.exe app-template\launch_seed_studio.py
)

