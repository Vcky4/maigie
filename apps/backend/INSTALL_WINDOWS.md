# Windows Development Setup

## Python Version Requirements

**Important**: This project supports Python 3.11 through 3.14.

- ✅ **Python 3.11** (recommended, matches Docker)
- ✅ **Python 3.12** (supported)
- ✅ **Python 3.13** (supported)
- ✅ **Python 3.14** (supported - uses `google-genai` SDK)
- ❌ **Python 3.15+** (not yet supported)

**Note**: Python 3.14 is fully supported. The project uses the modern `google-genai` SDK (v1.60.0+) which supports Python 3.14. The deprecated `google-generativeai` package is still used in some legacy code but doesn't affect Python 3.14 compatibility.

## Quick Install (Skip CUDA Dependencies)

Since CUDA packages (`nvidia-nccl-cu11`, etc.) are not available on Windows, use one of these methods:

### Method 1: Regenerate Lock File (Recommended - Clean Solution)

Regenerate the lock file on Windows to exclude CUDA packages:

```powershell
cd apps/backend
# Backup current lock file
cp poetry.lock poetry.lock.backup
# Remove and regenerate (excludes CUDA packages on Windows)
rm poetry.lock
poetry lock --no-update
poetry install
```

This creates a Windows-compatible lock file. Docker builds will still work (Linux has CUDA packages available).

### Method 2: Install and Ignore CUDA Errors

```powershell
cd apps/backend
poetry install 2>&1 | Select-String -Pattern "nvidia" -NotMatch
```

Poetry will fail on CUDA packages but install everything else. The errors are harmless since:
- CUDA packages are optional dependencies
- Production runs in Docker (Linux) where they're available
- Local development doesn't need them

### Method 3: Manual Install (If Methods 1-2 Fail)

```powershell
cd apps/backend
# Try normal install, ignore CUDA errors
poetry install || echo "Some packages failed (CUDA deps), continuing..."
# Install PyTorch separately
poetry run pip install torch --index-url https://download.pytorch.org/whl/cpu
```

## Alternative: Install with Error Tolerance

If you want to try installing everything and ignore CUDA errors:

```powershell
cd apps/backend
poetry install --no-root 2>&1 | Select-String -Pattern "nvidia" -NotMatch
```

Or simply ignore the CUDA errors - they won't affect local development since:
- Production runs in Docker (Linux) where CUDA packages are available
- Local development doesn't need CUDA (models run on CPU)
- The Docker build handles CUDA dependencies correctly

## After Installation

Install PyTorch separately (CPU version for local dev):

```powershell
poetry run pip install torch --index-url https://download.pytorch.org/whl/cpu
```

## Why This Happens

- `soprano-tts` and `transformers` have optional CUDA dependencies
- These dependencies don't have Windows wheels
- Poetry tries to resolve them anyway during `poetry install`
- **Solution**: Use `--no-extras` on Windows, or ignore the errors

## grpcio-tools on Windows

`grpcio-tools` requires Microsoft Visual C++ 14.0+ Build Tools to compile on Windows. It's configured to skip installation on Windows (similar to `soprano-tts`).

**Options:**

1. **Skip locally (Recommended)**: Proto files are generated in Docker. For local development, you can skip `grpcio-tools` installation.

2. **Install Build Tools**: If you need to generate proto files locally:
   - Download and install [Microsoft C++ Build Tools](https://visualstudio.microsoft.com/visual-cpp-build-tools/)
   - Then run: `poetry add grpcio-tools`

3. **Use Docker**: Generate proto files in Docker where build tools are available.

## Production (Docker)

The Docker build handles CUDA dependencies correctly:
- Runs on Linux where CUDA packages are available
- CUDA dependencies install successfully
- No issues in production
