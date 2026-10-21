# medNAMA PowerShell Launcher
Set-Location -Path $PSScriptRoot

$python = "python"
if (Test-Path ".venv-gpu\Scripts\python.exe") {
    $python = ".venv-gpu\Scripts\python.exe"
} elseif (Test-Path ".venv\Scripts\python.exe") {
    $python = ".venv\Scripts\python.exe"
}

& $python launcher.py @args

