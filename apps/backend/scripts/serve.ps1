# PowerShell script to run the FastAPI server with proper cleanup handling
$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$backendDir = Split-Path -Parent $scriptDir
Set-Location $backendDir

# Function to cleanup processes on port 8000
function Stop-ServerProcesses {
    Write-Host "`nShutting down server..." -ForegroundColor Yellow
    $connections = Get-NetTCPConnection -LocalPort 8000 -ErrorAction SilentlyContinue
    if ($connections) {
        $connections | ForEach-Object {
            if ($_.OwningProcess -gt 0) {
                Write-Host "Stopping process $($_.OwningProcess)..." -ForegroundColor Cyan
                Stop-Process -Id $_.OwningProcess -Force -ErrorAction SilentlyContinue
            }
        }
    }
}

# Set up trap handler for Ctrl+C and other termination signals
trap {
    Stop-ServerProcesses
    break
}

# Register cleanup on script exit
Register-EngineEvent PowerShell.Exiting -Action {
    Stop-ServerProcesses
} | Out-Null

# Use virtual environment Python if available, otherwise try poetry
try {
    Write-Host "Starting FastAPI server with auto-reload..." -ForegroundColor Green
    Write-Host "Server will automatically reload on file changes" -ForegroundColor Cyan
    Write-Host "Press Ctrl+C to stop the server`n" -ForegroundColor Yellow
    
    if (Test-Path ".venv\Scripts\python.exe") {
        & ".venv\Scripts\python.exe" -m uvicorn src.main:app --reload --host 0.0.0.0 --port 8000 --log-level info
    } elseif (Get-Command poetry -ErrorAction SilentlyContinue) {
        poetry run uvicorn src.main:app --reload --host 0.0.0.0 --port 8000 --log-level info
    } else {
        Write-Host "Error: Neither virtual environment nor Poetry found" -ForegroundColor Red
        exit 1
    }
} catch {
    Write-Host "`nError starting server: $_" -ForegroundColor Red
    Stop-ServerProcesses
    exit 1
} finally {
    # Cleanup on script exit (when Ctrl+C is pressed or server crashes)
    Stop-ServerProcesses
}

