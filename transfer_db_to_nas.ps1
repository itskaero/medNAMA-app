# One-click Database Transfer to NAS
$ErrorActionPreference = "Stop"
$repo = $PSScriptRoot
$py = Join-Path $repo ".venv-gpu\Scripts\python.exe"
if (-not (Test-Path $py)) {
    $py = "python"
}

& $py (Join-Path $repo "scripts\transfer_db_to_nas.py") @args

