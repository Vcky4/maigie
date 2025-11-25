# PowerShell script to run WebSocket verification using the correct Python environment
$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$backendDir = Split-Path -Parent $scriptDir
Set-Location $backendDir

Write-Host "Running WebSocket framework verification..." -ForegroundColor Cyan

# Use virtual environment Python if available, otherwise try poetry
if (Test-Path ".venv\Scripts\python.exe") {
    & ".venv\Scripts\python.exe" verify_websocket.py
    $exitCode = $LASTEXITCODE
} elseif (Get-Command poetry -ErrorAction SilentlyContinue) {
    poetry run python verify_websocket.py
    $exitCode = $LASTEXITCODE
} else {
    Write-Host "Error: Neither virtual environment nor Poetry found" -ForegroundColor Red
    Write-Host "Please run: poetry install" -ForegroundColor Yellow
    exit 1
}

exit $exitCode

