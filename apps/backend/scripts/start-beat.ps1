# PowerShell script to start Celery Beat scheduler for Maigie backend
# Usage: .\scripts\start-beat.ps1 [options]

param(
    [ValidateSet("debug", "info", "warning", "error")]
    [string]$LogLevel = "info"
)

$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$backendDir = Split-Path -Parent $scriptDir
Set-Location $backendDir

Write-Host "Starting Celery Beat scheduler..." -ForegroundColor Green
Write-Host "Log level: $LogLevel" -ForegroundColor Cyan
Write-Host ""

# Check if virtual environment exists
if (Test-Path ".venv\Scripts\python.exe") {
    $python = ".venv\Scripts\python.exe"
} elseif (Get-Command poetry -ErrorAction SilentlyContinue) {
    Write-Host "Using Poetry environment" -ForegroundColor Yellow
    $python = "poetry run python"
} else {
    Write-Host "Error: No virtual environment found. Please run 'poetry install' first." -ForegroundColor Red
    exit 1
}

# Start Celery Beat
& $python -m celery -A src.core.celery_app:celery_app beat `
    --loglevel=$LogLevel

