# Windows Installation Script
# Regenerates lock file to exclude Windows-incompatible packages:
# - CUDA dependencies (nvidia-nccl-cu11, etc.)
# - soprano-tts and lmdeploy (no Windows wheels available)
# Production runs on Linux (Docker) where these packages are available

Write-Host "Regenerating lock file to exclude Windows-incompatible packages..." -ForegroundColor Cyan
Write-Host "This will exclude: CUDA packages, soprano-tts, and lmdeploy" -ForegroundColor Gray

# Backup current lock file
if (Test-Path "poetry.lock") {
    Copy-Item "poetry.lock" "poetry.lock.backup"
    Write-Host "Backed up poetry.lock to poetry.lock.backup" -ForegroundColor Gray
}

# Remove and regenerate lock file (will exclude CUDA packages on Windows)
Remove-Item "poetry.lock" -ErrorAction SilentlyContinue
poetry lock --no-update

if ($LASTEXITCODE -eq 0) {
    Write-Host "`nInstalling dependencies..." -ForegroundColor Cyan
    poetry install
    
    if ($LASTEXITCODE -eq 0) {
        Write-Host "`nInstalling PyTorch (CPU version)..." -ForegroundColor Cyan
        poetry run pip install torch --index-url https://download.pytorch.org/whl/cpu
        
        Write-Host "`n✅ Installation complete!" -ForegroundColor Green
        Write-Host "Note: Lock file regenerated without Windows-incompatible packages:" -ForegroundColor Yellow
        Write-Host "  - CUDA dependencies (nvidia-nccl-cu11, etc.)" -ForegroundColor Yellow
        Write-Host "  - soprano-tts and lmdeploy (no Windows wheels)" -ForegroundColor Yellow
        Write-Host "Docker builds will still work (Linux has these packages available)" -ForegroundColor Yellow
        Write-Host "TTS functionality will not be available on Windows (use Docker for full features)" -ForegroundColor Yellow
    } else {
        Write-Host "`n⚠️  Installation had errors" -ForegroundColor Yellow
        exit 1
    }
} else {
    Write-Host "`n❌ Failed to regenerate lock file" -ForegroundColor Red
    Write-Host "Restoring backup..." -ForegroundColor Yellow
    if (Test-Path "poetry.lock.backup") {
        Copy-Item "poetry.lock.backup" "poetry.lock"
    }
    exit 1
}
