@echo off
title medNAMA Control Hub (Electron Desktop)
cd /d "%~dp0"

echo ====================================================================
echo             medNAMA Unified Control Hub - Electron Desktop
echo ====================================================================

if not exist "electron-launcher\node_modules\electron" (
    echo Installing electron dependencies...
    cd electron-launcher && npm install && cd ..
)

echo Launching medNAMA Desktop App...
cd electron-launcher
npx electron .

