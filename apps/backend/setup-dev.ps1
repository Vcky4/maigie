# PowerShell script to set up development environment

Write-Host "Setting up Maigie Backend Development Environment" -ForegroundColor Green
Write-Host "================================================" -ForegroundColor Green
Write-Host ""

# Check Python version
Write-Host "Checking Python installation..." -ForegroundColor Yellow
$pythonCmd = $null
$pythonPaths = @("python", "python3", "py")

foreach ($cmd in $pythonPaths) {
    try {
        $version = & $cmd --version 2>&1
        if ($LASTEXITCODE -eq 0 -and $version -match "Python") {
            $pythonCmd = $cmd
            Write-Host "[OK] Found: $version" -ForegroundColor Green
            
            # Check if Python 3.11+
            $versionMatch = $version -match "Python (\d+)\.(\d+)"
            if ($versionMatch) {
                $major = [int]$matches[1]
                $minor = [int]$matches[2]
                if ($major -lt 3 -or ($major -eq 3 -and $minor -lt 11)) {
                    $versionString = "$major.$minor"
                    Write-Host "[WARNING] Python 3.11+ is recommended. Current version: $versionString" -ForegroundColor Yellow
                }
            }
            break
        }
    } catch {
        continue
    }
}

if (-not $pythonCmd) {
    Write-Host "[ERROR] Python not found. Please install Python 3.11+ from:" -ForegroundColor Red
    Write-Host "  https://www.python.org/downloads/" -ForegroundColor Cyan
    Write-Host "  Or Windows Store: ms-windows-store://pdp/?ProductId=9NRWMJP3717K" -ForegroundColor Cyan
    Write-Host ""
    Write-Host "After installing Python, restart your terminal and run this script again." -ForegroundColor Yellow
    exit 1
}

# Check Poetry
Write-Host ""
Write-Host "Checking Poetry installation..." -ForegroundColor Yellow
if (-not (Get-Command poetry -ErrorAction SilentlyContinue)) {
    Write-Host "[INFO] Poetry not found. Installing..." -ForegroundColor Yellow
    
    # Get script directory
    $scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
    
    # Run Poetry installer
    & "$scriptDir\install-poetry.ps1"
    
    # Refresh PATH
    $userPath = [System.Environment]::GetEnvironmentVariable("Path", "User")
    $machinePath = [System.Environment]::GetEnvironmentVariable("Path", "Machine")
    $env:Path = "$userPath;$machinePath"
    
    # Check again after installation
    if (-not (Get-Command poetry -ErrorAction SilentlyContinue)) {
        Write-Host "[WARNING] Poetry installer completed but not found in PATH." -ForegroundColor Yellow
        Write-Host "Trying pip installation as fallback..." -ForegroundColor Yellow
        
        # Try pip installation
        try {
            & $pythonCmd -m pip install poetry
            if ($LASTEXITCODE -eq 0) {
                Write-Host "[OK] Poetry installed via pip" -ForegroundColor Green
                # Refresh PATH again
                $userPath = [System.Environment]::GetEnvironmentVariable("Path", "User")
                $machinePath = [System.Environment]::GetEnvironmentVariable("Path", "Machine")
                $env:Path = "$userPath;$machinePath"
            }
        } catch {
            Write-Host "[ERROR] Poetry installation failed. Please install manually:" -ForegroundColor Red
            Write-Host "  Option 1: pip install poetry" -ForegroundColor Cyan
            Write-Host "  Option 2: https://python-poetry.org/docs/#installation" -ForegroundColor Cyan
            exit 1
        }
        
        # Final check
        if (-not (Get-Command poetry -ErrorAction SilentlyContinue)) {
            Write-Host "[ERROR] Poetry still not found. Please restart your terminal and try again." -ForegroundColor Red
            Write-Host "Or install manually: pip install poetry" -ForegroundColor Yellow
            exit 1
        }
    }
}

Write-Host "[OK] Poetry is installed" -ForegroundColor Green
poetry --version

# Configure Poetry for in-project virtual environment
Write-Host ""
Write-Host "Configuring Poetry for in-project virtual environment..." -ForegroundColor Yellow
$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $scriptDir
poetry config virtualenvs.in-project true
Write-Host "[OK] Poetry configured to create virtual environment in project directory (.venv)" -ForegroundColor Green

# Install dependencies (this will create the virtual environment)
Write-Host ""
Write-Host "Installing dependencies..." -ForegroundColor Yellow
Write-Host "This will create a virtual environment in .venv and install all dependencies." -ForegroundColor Gray
poetry install

if ($LASTEXITCODE -eq 0) {
    Write-Host "[OK] Dependencies installed successfully" -ForegroundColor Green
} else {
    Write-Host "[ERROR] Failed to install dependencies" -ForegroundColor Red
    exit 1
}

# Create .env file if it doesn't exist
if (-not (Test-Path ".env")) {
    Write-Host ""
    Write-Host "Creating .env file from .env.example..." -ForegroundColor Yellow
    if (Test-Path ".env.example") {
        Copy-Item ".env.example" ".env"
        Write-Host "[OK] .env file created" -ForegroundColor Green
    } else {
        Write-Host "[WARNING] .env.example not found, skipping..." -ForegroundColor Yellow
    }
}

# Verify setup
Write-Host ""
Write-Host "Verifying setup..." -ForegroundColor Yellow
try {
    poetry run python verify_setup.py
    if ($LASTEXITCODE -eq 0) {
        Write-Host ""
        Write-Host "[SUCCESS] Setup complete! You can now start the server with:" -ForegroundColor Green
        Write-Host "  poetry run uvicorn src.main:app --reload" -ForegroundColor Cyan
        Write-Host "  or" -ForegroundColor Gray
        Write-Host "  nx serve backend" -ForegroundColor Cyan
    } else {
        Write-Host "[WARNING] Verification script returned non-zero exit code" -ForegroundColor Yellow
    }
} catch {
    Write-Host "[WARNING] Verification script failed, but setup may still be correct." -ForegroundColor Yellow
    Write-Host "  Error: $_" -ForegroundColor Red
}
