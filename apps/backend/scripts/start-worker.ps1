# PowerShell script to start Celery worker for Maigie backend
# Usage: .\scripts\start-worker.ps1 [options]

param(
    [string]$Name = "worker@%h",
    [string]$Queue = "default",
    [int]$Concurrency = 4,
    [ValidateSet("debug", "info", "warning", "error")]
    [string]$LogLevel = "info"
)

$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$backendDir = Split-Path -Parent $scriptDir
Set-Location $backendDir

Write-Host "Starting Celery worker..." -ForegroundColor Green
Write-Host "Worker name: $Name" -ForegroundColor Cyan
Write-Host "Queue: $Queue" -ForegroundColor Cyan
Write-Host "Concurrency: $Concurrency" -ForegroundColor Cyan
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

# Start Celery worker
& $python -m celery -A src.core.celery_app:celery_app worker `
    --loglevel=$LogLevel `
    --concurrency=$Concurrency `
    --hostname=$Name `
    --queues=$Queue `
    --without-gossip `
    --without-mingle `
    --without-heartbeat

