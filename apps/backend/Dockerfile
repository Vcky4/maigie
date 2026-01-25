# Copyright (C) 2025 Maigie
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU Affero General Public License as published
# by the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU Affero General Public License for more details.
#
# You should have received a copy of the GNU Affero General Public License
# along with this program.  If not, see <https://www.gnu.org/licenses/>.

FROM python:3.12-slim AS builder
WORKDIR /app

# Build argument to choose PyTorch version (cpu or cuda)
ARG PYTORCH_VERSION=cpu
ARG CUDA_VERSION=cu121

# Install build dependencies needed for lmdeploy (CMake, Ninja, rapidjson-dev, etc.)
RUN apt-get update && apt-get install -y \
  build-essential \
  curl \
  cmake \
  ninja-build \
  rapidjson-dev \
  git \
  && rm -rf /var/lib/apt/lists/*

RUN curl -sSL https://install.python-poetry.org | python3 -
ENV PATH="/root/.local/bin:$PATH"
# Configure Poetry to NOT create virtualenvs BEFORE copying files
RUN poetry config virtualenvs.create false
COPY pyproject.toml poetry.lock ./
# Regenerate lock file on Linux to get Linux-compatible wheels
RUN poetry lock --no-interaction

# Remove torch and lmdeploy from lock file to prevent Poetry from installing them
# We install torch separately to avoid CUDA dependency issues
# We install lmdeploy separately because it has ABI compatibility issues with Poetry
RUN python3 << 'EOF'
import sys
lines = open('poetry.lock').readlines()
output = []
skip = False
pkgs = {'torch', 'torchvision', 'torchaudio', 'lmdeploy'}
i = 0
while i < len(lines):
    line = lines[i]
    if line.strip() == '[[package]]':
        skip = False
        if i + 1 < len(lines) and 'name = "' in lines[i + 1]:
            pkg_name = lines[i + 1].split('name = "')[1].split('"')[0]
            skip = pkg_name in pkgs
    if not skip:
        # Check if line contains any package references we want to skip
        should_skip_line = False
        for p in pkgs:
            if p + ' =' in line:
                should_skip_line = True
                break
        if not should_skip_line:
            output.append(line)
    i += 1
open('poetry.lock', 'w').writelines(output)
EOF

# Install Poetry dependencies first (without torch and lmdeploy to avoid issues)
# Config is already set above, but ensure it's still false
RUN poetry config virtualenvs.create false && \
  poetry config virtualenvs.in-project false && \
  poetry install --no-root --no-interaction --no-ansi --without dev

# If Poetry still created a venv (despite config), copy packages to system Python
RUN if [ -d "/root/.cache/pypoetry/virtualenvs" ]; then \
  VENV_PATH=$(find /root/.cache/pypoetry/virtualenvs -type d -name "site-packages" | head -1); \
  if [ -n "$VENV_PATH" ]; then \
    echo "⚠️  Poetry created venv despite config, copying packages to system Python..."; \
    cp -r "$VENV_PATH"/* /usr/local/lib/python3.12/site-packages/ 2>/dev/null || true; \
  fi; \
  fi

# Verify critical packages are installed (fail if required packages are missing)
RUN python3 -c "import prisma; print('✅ Prisma installed')" || \
  (echo "❌ ERROR: Prisma is required but not installed!" && exit 1) && \
  python3 -c "import fastapi; print('✅ FastAPI installed')" || \
  (echo "❌ ERROR: FastAPI is required but not installed!" && exit 1) && \
  echo "Core dependencies verified"

# Install PyTorch AFTER Poetry (to avoid duplication and save space)
# CUDA version works on both GPU and CPU (falls back to CPU if GPU unavailable)
RUN if [ "$PYTORCH_VERSION" = "cuda" ]; then \
  echo "Installing CUDA-enabled PyTorch (CUDA ${CUDA_VERSION})..." && \
  pip install --no-cache-dir torch torchvision torchaudio --index-url https://download.pytorch.org/whl/${CUDA_VERSION} && \
  pip cache purge; \
  else \
  echo "Installing CPU-only PyTorch..." && \
  pip install --no-cache-dir torch torchvision torchaudio --index-url https://download.pytorch.org/whl/cpu && \
  pip cache purge; \
  fi

# Install lmdeploy ONLY if not already present (optional, for soprano-tts performance)
# CRITICAL: Use constraints file to prevent PyTorch reinstallation (saves ~2GB+ disk space)
# soprano-tts is already installed via Poetry and works fine without lmdeploy (just slower)
RUN python3 << 'PYEOF'
import sys
import subprocess

# Check if lmdeploy is already installed
try:
    import lmdeploy
    print(f"✅ lmdeploy already installed (version: {getattr(lmdeploy, '__version__', 'unknown')})")
except ImportError:
    # Get installed PyTorch versions to create constraints file
    try:
        import torch
        torch_version = torch.__version__
        try:
            import torchvision
            tv_version = torchvision.__version__
        except:
            tv_version = None
        try:
            import torchaudio
            ta_version = torchaudio.__version__
        except:
            ta_version = None
        
        # Create constraints file to prevent PyTorch reinstallation
        with open('/tmp/pytorch-constraints.txt', 'w') as f:
            f.write(f'torch=={torch_version}\n')
            if tv_version:
                f.write(f'torchvision=={tv_version}\n')
            if ta_version:
                f.write(f'torchaudio=={ta_version}\n')
        
        print(f"✅ Created constraints file pinning PyTorch {torch_version}")
        
        # Install lmdeploy with constraints
        print("Installing lmdeploy (optional, for TTS performance)...")
        result = subprocess.run([
            sys.executable, "-m", "pip", "install",
            "--no-cache-dir", "--constraint", "/tmp/pytorch-constraints.txt", "lmdeploy"
        ], capture_output=True, text=True)
        
        if result.returncode == 0:
            print("✅ lmdeploy installed successfully")
        else:
            print("⚠️  lmdeploy installation failed (TTS will use transformers backend)")
            print("This is acceptable - soprano-tts works without lmdeploy, just slower")
            if result.stderr:
                print(f"Error: {result.stderr[-300:]}")
    except Exception as e:
        print(f"⚠️  Could not install lmdeploy: {e}")
        print("TTS will use transformers backend (slower but functional)")
PYEOF
pip cache purge || true && \
rm -rf /root/.cache/pip && \
rm -rf /tmp/*

# Verify soprano-tts is available (should be from Poetry, don't reinstall to avoid PyTorch issues)
RUN python3 -c "import soprano_tts; print('✅ soprano-tts available (from Poetry)')" || \
  (echo "⚠️  WARNING: soprano-tts not available - TTS will not be available" && exit 0)

# Remove build dependencies AFTER installing packages that might need them
# Keep git until after lmdeploy is confirmed installed (for fallback git installs)
RUN apt-get purge -y \
  build-essential \
  cmake \
  ninja-build \
  && apt-get autoremove -y \
  && apt-get clean

# Verify lmdeploy installation before removing git
RUN python3 -c "import lmdeploy; print('✅ lmdeploy successfully installed (version:', getattr(lmdeploy, '__version__', 'unknown'), ')')" || \
  (python3 -c "print('⚠️  WARNING: lmdeploy is not installed - soprano-tts will use transformers backend (slower)')" && exit 0)

# Now remove git (no longer needed)
RUN apt-get purge -y git && apt-get autoremove -y && apt-get clean

# Clean up build artifacts and caches aggressively to save space
# This also removes the constraints file from /tmp/
RUN rm -rf /root/.cache/pip && \
  rm -rf /root/.cache/pypoetry && \
  rm -rf /tmp/* && \
  rm -rf /var/lib/apt/lists/* && \
  rm -rf /usr/share/doc/* && \
  rm -rf /usr/share/man/* && \
  find /usr/local/lib/python3.12/site-packages -type d -name "__pycache__" -exec rm -rf {} + 2>/dev/null || true && \
  find /usr/local/lib/python3.12/site-packages -name "*.pyc" -delete 2>/dev/null || true && \
  find /usr/local/lib/python3.12/site-packages -name "*.pyo" -delete 2>/dev/null || true && \
  find /usr/local/lib/python3.12/site-packages -type d -name "*.dist-info" -exec rm -rf {} + 2>/dev/null || true && \
  find /usr/local/lib/python3.12/site-packages -type d -name "*.egg-info" -exec rm -rf {} + 2>/dev/null || true

# Verify Prisma is installed (required dependency)
RUN python3 -c "import prisma; print('✅ Prisma Python package installed')" || \
  (echo "❌ ERROR: Prisma is required but not installed!" && exit 1)

FROM python:3.12-slim
WORKDIR /app

# Install runtime dependencies
# For GPU support, nvidia-container-toolkit must be installed on the host
RUN apt-get update && apt-get install -y --no-install-recommends \
  libpq-dev \
  curl \
  libatomic1 \
  postgresql-client \
  && rm -rf /var/lib/apt/lists/* \
  && rm -rf /usr/share/doc/* \
  && rm -rf /usr/share/man/*
COPY --from=builder /usr/local/lib/python3.12/site-packages /usr/local/lib/python3.12/site-packages
COPY --from=builder /usr/local/bin /usr/local/bin
COPY . .
# Generate Prisma Client - Prisma is required, so fail if it's not available
RUN python3 -c "import prisma; print('✅ Prisma available')" || \
  (echo "❌ ERROR: Prisma is required but not found in final stage!" && exit 1) && \
  python3 -m prisma generate
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --start-period=5s --retries=3 \
  CMD curl -f http://localhost:8000/health || exit 1
CMD ["uvicorn", "src.main:app", "--host", "0.0.0.0", "--port", "8000"]