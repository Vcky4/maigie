# PowerShell script to run the FastAPI server
$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$backendDir = Split-Path -Parent $scriptDir
Set-Location $backendDir

# Use virtual environment Python if available, otherwise try poetry
if (Test-Path ".venv\Scripts\python.exe") {
    & ".venv\Scripts\python.exe" -m uvicorn src.main:app --reload --host 0.0.0.0 --port 8000
} elseif (Get-Command poetry -ErrorAction SilentlyContinue) {
    poetry run uvicorn src.main:app --reload --host 0.0.0.0 --port 8000
} else {
    Write-Host "Error: Neither virtual environment nor Poetry found" -ForegroundColor Red
    exit 1
}

