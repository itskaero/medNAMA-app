@echo off
rem PaedNAMA's web app uses medNAMA's installed packages (one copy of React and Next for both apps).
rem Run once after cloning, after "npm install" in frontend\.
cd /d "%~dp0..\web"
if exist node_modules (echo node_modules already linked & exit /b 0)
mklink /J node_modules ..\..\..\frontend\node_modules
