# medNAMA Electron Desktop Hub Launcher
Set-Location -Path $PSScriptRoot

Write-Host "====================================================================" -ForegroundColor Cyan
Write-Host "         medNAMA Unified Control Hub - Electron Desktop" -ForegroundColor White
Write-Host "====================================================================" -ForegroundColor Cyan

if (-not (Test-Path "electron-launcher\node_modules\electron")) {
    Write-Host "Installing electron dependencies..." -ForegroundColor Yellow
    Push-Location "electron-launcher"
    npm install
    Pop-Location
}

Write-Host "Launching medNAMA Desktop App..." -ForegroundColor Green
Push-Location "electron-launcher"
npx electron .
Pop-Location

