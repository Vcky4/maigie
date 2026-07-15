# PowerShell script to run the NEW domain-driven FastAPI server
# Uses src.app:app (the refactored architecture)
$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$backendDir = Split-Path -Parent $scriptDir
Set-Location $backendDir

function Stop-ServerProcesses {
    Write-Host "`nShutting down server..." -ForegroundColor Yellow
    $connections = Get-NetTCPConnection -LocalPort 8000 -ErrorAction SilentlyContinue
    if ($connections) {
        $connections | ForEach-Object {
            if ($_.OwningProcess -gt 0) {
                Stop-Process -Id $_.OwningProcess -Force -ErrorAction SilentlyContinue
            }
        }
    }
}

trap {
    Stop-ServerProcesses
    break
}

try {
    Write-Host "Starting Maigie API (domain-driven architecture)..." -ForegroundColor Green
    Write-Host "Endpoints: /api/v1/{identity,knowledge,learning,spaces,classrooms,intelligence,progress,billing,admin}" -ForegroundColor Cyan
    Write-Host "Docs: http://localhost:8000/redoc" -ForegroundColor Cyan
    Write-Host "Press Ctrl+C to stop`n" -ForegroundColor Yellow
    
    if (Test-Path ".venv\Scripts\python.exe") {
        & ".venv\Scripts\python.exe" -m uvicorn src.app:app --reload --host 0.0.0.0 --port 8000 --log-level info
    } elseif (Get-Command poetry -ErrorAction SilentlyContinue) {
        poetry run uvicorn src.app:app --reload --host 0.0.0.0 --port 8000 --log-level info
    } else {
        Write-Host "Error: Neither virtual environment nor Poetry found" -ForegroundColor Red
        exit 1
    }
} catch {
    Write-Host "`nError: $_" -ForegroundColor Red
    Stop-ServerProcesses
    exit 1
} finally {
    Stop-ServerProcesses
}
