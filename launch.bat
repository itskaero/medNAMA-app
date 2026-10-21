@echo off
title medNAMA Control Hub
cd /d "%~dp0"

echo ====================================================================
echo                   medNAMA Unified Launcher
echo ====================================================================

rem Find best python interpreter
set PYTHON=python
if exist ".venv-gpu\Scripts\python.exe" (
    set PYTHON=.venv-gpu\Scripts\python.exe
) else if exist ".venv\Scripts\python.exe" (
    set PYTHON=.venv\Scripts\python.exe
)

rem Launch interactive launcher
"%PYTHON%" launcher.py %*
pause

