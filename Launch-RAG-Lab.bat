@echo off
title Launch medNAMA RAG Experiment Lab
cd /d "%~dp0"
if exist "app-template\dist\Launch-RAG-Lab\Launch-RAG-Lab.exe" (
    start "" "app-template\dist\Launch-RAG-Lab\Launch-RAG-Lab.exe"
) else (
    echo [ERROR] Compiled executable not found, launching Python fallback...
    .venv-gpu\Scripts\python.exe app-template\launch_rag_lab.py
)

