#!/usr/bin/env pwsh
# Stop Nx backend server and all related processes

Write-Host "Stopping backend server..." -ForegroundColor Yellow

# Stop processes on port 8000
$connections = Get-NetTCPConnection -LocalPort 8000 -ErrorAction SilentlyContinue
if ($connections) {
    $connections | ForEach-Object {
        if ($_.OwningProcess -gt 0) {
            Write-Host "Stopping process $($_.OwningProcess) on port 8000..." -ForegroundColor Cyan
            Stop-Process -Id $_.OwningProcess -Force -ErrorAction SilentlyContinue
        }
    }
} else {
    Write-Host "No processes found on port 8000" -ForegroundColor Gray
}

# Stop Node processes (Nx)
$nodeProcesses = Get-Process node -ErrorAction SilentlyContinue
if ($nodeProcesses) {
    $nodeProcesses | ForEach-Object {
        Write-Host "Stopping Node process $($_.Id)..." -ForegroundColor Cyan
        Stop-Process -Id $_.Id -Force -ErrorAction SilentlyContinue
    }
} else {
    Write-Host "No Node processes found" -ForegroundColor Gray
}

# Stop Python processes that might be uvicorn
$pythonProcesses = Get-Process python -ErrorAction SilentlyContinue
if ($pythonProcesses) {
    $pythonProcesses | ForEach-Object {
        $hasPort8000 = Get-NetTCPConnection -OwningProcess $_.Id -ErrorAction SilentlyContinue | Where-Object { $_.LocalPort -eq 8000 }
        if ($hasPort8000) {
            Write-Host "Stopping Python process $($_.Id) (uvicorn)..." -ForegroundColor Cyan
            Stop-Process -Id $_.Id -Force -ErrorAction SilentlyContinue
        }
    }
}

Write-Host "`n✅ Server stopped!" -ForegroundColor Green
Write-Host "`nRemaining processes on port 8000:" -ForegroundColor Yellow
Get-NetTCPConnection -LocalPort 8000 -ErrorAction SilentlyContinue | Select-Object LocalPort, OwningProcess, State | Format-Table -AutoSize

