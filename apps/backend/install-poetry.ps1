# PowerShell script to install Poetry on Windows

Write-Host "Installing Poetry..." -ForegroundColor Green

# Check if Poetry is already installed
if (Get-Command poetry -ErrorAction SilentlyContinue) {
    Write-Host "[OK] Poetry is already installed!" -ForegroundColor Green
    poetry --version
    exit 0
}

# Check if Python is available
Write-Host "Checking for Python..." -ForegroundColor Yellow
$pythonCmd = $null
$pythonPaths = @("python", "python3", "py")

foreach ($cmd in $pythonPaths) {
    try {
        $version = & $cmd --version 2>&1
        if ($LASTEXITCODE -eq 0 -and $version -match "Python") {
            $pythonCmd = $cmd
            Write-Host "[OK] Found Python: $version" -ForegroundColor Green
            break
        }
    } catch {
        continue
    }
}

if (-not $pythonCmd) {
    Write-Host "[ERROR] Python is required to install Poetry." -ForegroundColor Red
    Write-Host "Please install Python 3.7+ from https://www.python.org/" -ForegroundColor Yellow
    Write-Host "Or use the Windows Store: ms-windows-store://pdp/?ProductId=9NRWMJP3717K" -ForegroundColor Cyan
    Write-Host ""
    Write-Host "Alternative: Install Poetry manually using pipx:" -ForegroundColor Yellow
    Write-Host "  pip install pipx" -ForegroundColor Cyan
    Write-Host "  pipx install poetry" -ForegroundColor Cyan
    exit 1
}

# Try pipx installation first (recommended method)
Write-Host ""
Write-Host "Attempting installation with pipx (recommended)..." -ForegroundColor Yellow
try {
    # Check if pipx is available
    $pipxAvailable = Get-Command pipx -ErrorAction SilentlyContinue
    if ($pipxAvailable) {
        Write-Host "[OK] pipx found, installing Poetry..." -ForegroundColor Green
        & pipx install poetry
        if ($LASTEXITCODE -eq 0) {
            Write-Host "[SUCCESS] Poetry installed via pipx!" -ForegroundColor Green
            # Refresh PATH
            $env:Path = [System.Environment]::GetEnvironmentVariable("Path", "User") + ";" + [System.Environment]::GetEnvironmentVariable("Path", "Machine")
            $pipxBinPath = "$env:USERPROFILE\.local\bin"
            if (Test-Path $pipxBinPath) {
                $env:Path += ";$pipxBinPath"
            }
            goto VerifyInstallation
        }
    } else {
        Write-Host "[INFO] pipx not found, trying official installer..." -ForegroundColor Yellow
    }
} catch {
    Write-Host "[INFO] pipx installation failed, trying official installer..." -ForegroundColor Yellow
}

# Install Poetry using the official installer
Write-Host ""
Write-Host "Downloading Poetry installer..." -ForegroundColor Yellow
try {
    $installerUrl = "https://install.python-poetry.org"
    $installerScript = Invoke-WebRequest -Uri $installerUrl -UseBasicParsing -ErrorAction Stop
    
    Write-Host "Running Poetry installer with Python..." -ForegroundColor Yellow
    
    # Save to temp file
    $tempFile = [System.IO.Path]::GetTempFileName() + ".py"
    $installerScript.Content | Out-File -FilePath $tempFile -Encoding UTF8 -NoNewline
    
    # Run with Python (without version flag - installer handles this)
    & $pythonCmd $tempFile
    
    # Clean up
    Remove-Item $tempFile -ErrorAction SilentlyContinue
    
    Write-Host ""
    Write-Host "[OK] Poetry installer completed" -ForegroundColor Green
    
} catch {
    Write-Host "[ERROR] Failed to download or run Poetry installer" -ForegroundColor Red
    Write-Host "Error: $_" -ForegroundColor Red
    Write-Host ""
    Write-Host "Trying alternative: pip installation..." -ForegroundColor Yellow
    
    # Try pip as fallback
    try {
        & $pythonCmd -m pip install poetry
        if ($LASTEXITCODE -eq 0) {
            Write-Host "[SUCCESS] Poetry installed via pip!" -ForegroundColor Green
            # Get Python Scripts directory for pip installations
            $pythonScripts = & $pythonCmd -c "import sysconfig; print(sysconfig.get_path('scripts'))" 2>&1
            if ($pythonScripts -and (Test-Path $pythonScripts)) {
                $env:Path += ";$pythonScripts"
                Write-Host "[OK] Added Python Scripts to PATH: $pythonScripts" -ForegroundColor Green
            }
        }
    } catch {
        Write-Host "[ERROR] pip installation also failed" -ForegroundColor Red
    }
    
    Write-Host ""
    Write-Host "Alternative installation methods:" -ForegroundColor Yellow
    Write-Host "1. Using pipx (recommended):" -ForegroundColor Cyan
    Write-Host "   pip install pipx" -ForegroundColor Gray
    Write-Host "   pipx install poetry" -ForegroundColor Gray
    Write-Host ""
    Write-Host "2. Using pip:" -ForegroundColor Cyan
    Write-Host "   pip install poetry" -ForegroundColor Gray
    Write-Host ""
    Write-Host "3. Manual installation:" -ForegroundColor Cyan
    Write-Host "   https://python-poetry.org/docs/#installation" -ForegroundColor Gray
    exit 1
}

# Refresh PATH
Write-Host "Refreshing PATH..." -ForegroundColor Yellow
$userPath = [System.Environment]::GetEnvironmentVariable("Path", "User")
$machinePath = [System.Environment]::GetEnvironmentVariable("Path", "Machine")
$env:Path = "$userPath;$machinePath"

# Common Poetry installation paths (check in order)
$poetryPaths = @(
    "$env:USERPROFILE\.local\bin",
    "$env:APPDATA\Python\Scripts",
    "$env:LOCALAPPDATA\Programs\Python\Python*\Scripts",
    "$env:LOCALAPPDATA\Python\pythoncore-*\Scripts"
)

# Also check for pipx installation
$pipxBinPath = "$env:USERPROFILE\.local\bin"
if (Test-Path $pipxBinPath) {
    $env:Path += ";$pipxBinPath"
}

# Get Python Scripts directory (for pip installations)
try {
    $pythonScripts = & $pythonCmd -c "import sysconfig; print(sysconfig.get_path('scripts'))" 2>&1
    if ($pythonScripts -and (Test-Path $pythonScripts)) {
        $env:Path += ";$pythonScripts"
        Write-Host "[OK] Added Python Scripts to PATH: $pythonScripts" -ForegroundColor Green
    }
} catch {
    # Ignore if we can't get Python scripts path
}

foreach ($pathPattern in $poetryPaths) {
    # Handle wildcards
    if ($pathPattern -match '\*') {
        $parentPath = Split-Path $pathPattern -Parent
        if (Test-Path $parentPath) {
            $matchingDirs = Get-ChildItem -Path $parentPath -Directory -Filter "*" -ErrorAction SilentlyContinue
            foreach ($dir in $matchingDirs) {
                $scriptsPath = Join-Path $dir.FullName "Scripts"
                if (Test-Path "$scriptsPath\poetry.exe") {
                    $env:Path += ";$scriptsPath"
                    Write-Host "[OK] Found Poetry at: $scriptsPath" -ForegroundColor Green
                }
            }
        }
    } else {
        if (Test-Path "$pathPattern\poetry.exe") {
            $env:Path += ";$pathPattern"
            Write-Host "[OK] Found Poetry at: $pathPattern" -ForegroundColor Green
        }
    }
}

# Check Poetry's default installation location
$poetryHome = [System.Environment]::GetEnvironmentVariable("POETRY_HOME")
if (-not $poetryHome) {
    $poetryHome = "$env:USERPROFILE\.poetry"
}
if (Test-Path "$poetryHome\bin\poetry.exe") {
    $env:Path += ";$poetryHome\bin"
    Write-Host "[OK] Found Poetry at: $poetryHome\bin" -ForegroundColor Green
}
# Verify installation
Write-Host ""
Write-Host "Verifying installation..." -ForegroundColor Yellow
Start-Sleep -Seconds 3

# Try to find poetry in common locations
$poetryFound = $false
if (Get-Command poetry -ErrorAction SilentlyContinue) {
    $poetryFound = $true
} else {
    # Check if poetry.exe exists in any of the paths we added
    $allPaths = $env:Path -split ';'
    foreach ($path in $allPaths) {
        if (Test-Path "$path\poetry.exe") {
            $poetryFound = $true
            break
        }
    }
}

if ($poetryFound -or (Get-Command poetry -ErrorAction SilentlyContinue)) {
    Write-Host "[SUCCESS] Poetry installed successfully!" -ForegroundColor Green
    poetry --version
    Write-Host ""
    Write-Host "Note: If Poetry is not found in new terminals, restart your terminal" -ForegroundColor Yellow
    Write-Host "or add Poetry to your PATH manually." -ForegroundColor Yellow
} else {
    Write-Host "[WARNING] Poetry installation completed, but not found in PATH." -ForegroundColor Yellow
    Write-Host ""
    Write-Host "Poetry may be installed at one of these locations:" -ForegroundColor Cyan
    Write-Host "  - $env:USERPROFILE\.local\bin" -ForegroundColor Gray
    Write-Host "  - $env:APPDATA\Python\Scripts" -ForegroundColor Gray
    Write-Host "  - $poetryHome\bin" -ForegroundColor Gray
    Write-Host ""
    Write-Host "To add Poetry to PATH manually:" -ForegroundColor Yellow
    Write-Host "1. Find the Poetry installation directory (check the locations above)" -ForegroundColor Gray
    Write-Host "2. Add it to your PATH environment variable" -ForegroundColor Gray
    Write-Host "3. Restart your terminal" -ForegroundColor Gray
    Write-Host ""
    Write-Host "Or try restarting your terminal - Poetry may be available after restart." -ForegroundColor Cyan
    Write-Host ""
    Write-Host "You can also try:" -ForegroundColor Yellow
    Write-Host "  pip install poetry" -ForegroundColor Cyan
}
