# Regenerating Poetry Lock File (Windows)

## Problem

The `poetry.lock` file includes CUDA dependencies (`nvidia-nccl-cu11`, `nvidia-cublas-cu11`, etc.) that don't have Windows wheels, causing installation failures on Windows.

## Solution: Regenerate Lock File on Windows

Since you're developing on Windows and deploying to Linux (Docker), regenerate the lock file on Windows to exclude CUDA packages:

```powershell
cd apps/backend

# Backup current lock file (optional)
cp poetry.lock poetry.lock.backup

# Remove lock file
rm poetry.lock

# Regenerate lock file (will exclude CUDA packages on Windows)
poetry lock --no-update

# Install dependencies
poetry install
```

## What This Does

- **On Windows**: Generates a lock file without CUDA packages (they're not available)
- **In Docker (Linux)**: Poetry will install CUDA packages if available, or skip them if not
- **Result**: Works on both Windows and Linux

## Important Notes

⚠️ **This will change the lock file** - make sure to commit it so Docker builds use the updated lock file.

The lock file will be platform-aware:
- Windows: No CUDA packages
- Linux: CUDA packages available (but optional)

## Alternative: Keep Current Lock File

If you prefer to keep the current lock file:
1. Use `poetry install` and ignore CUDA errors (they're harmless)
2. Install PyTorch separately: `poetry run pip install torch --index-url https://download.pytorch.org/whl/cpu`
3. Docker builds will work fine (Linux has CUDA packages)
