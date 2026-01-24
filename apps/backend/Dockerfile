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
COPY pyproject.toml poetry.lock ./
# Regenerate lock file on Linux to get Linux-compatible wheels
RUN poetry lock --no-interaction

# Install PyTorch FIRST (before Poetry) to avoid duplication
# CUDA version works on both GPU and CPU (falls back to CPU if GPU unavailable)
RUN if [ "$PYTORCH_VERSION" = "cuda" ]; then \
        echo "Installing CUDA-enabled PyTorch (CUDA ${CUDA_VERSION})..." && \
        pip install --no-cache-dir torch torchvision torchaudio --index-url https://download.pytorch.org/whl/${CUDA_VERSION}; \
    else \
        echo "Installing CPU-only PyTorch..." && \
        pip install --no-cache-dir torch torchvision torchaudio --index-url https://download.pytorch.org/whl/cpu; \
    fi && \
    pip cache purge

# Install Poetry dependencies (Poetry will skip torch since it's already installed)
RUN poetry config virtualenvs.create false && \
    poetry install --no-root --no-interaction --no-ansi --without dev || true

# Clean up build artifacts and caches aggressively to save space
RUN rm -rf /root/.cache/pip && \
    rm -rf /root/.cache/pypoetry && \
    rm -rf /tmp/* && \
    find /usr/local/lib/python3.12/site-packages -type d -name "__pycache__" -exec rm -rf {} + 2>/dev/null || true && \
    find /usr/local/lib/python3.12/site-packages -name "*.pyc" -delete 2>/dev/null || true && \
    find /usr/local/lib/python3.12/site-packages -name "*.pyo" -delete 2>/dev/null || true

# Install lmdeploy - required for soprano-tts performance
# Try multiple installation methods to ensure it works
RUN sh -c 'if ! python3 -c "import lmdeploy" 2>/dev/null; then \
    echo "Installing lmdeploy..."; \
    pip install --no-cache-dir lmdeploy && echo "lmdeploy installed via pip wheels" || \
    (echo "Trying lmdeploy without TurboMind..." && \
     DISABLE_TURBOMIND=1 pip install --no-cache-dir git+https://github.com/InternLM/lmdeploy.git && \
     echo "lmdeploy installed from source (without TurboMind)") || \
    (echo "Trying full lmdeploy source install..." && \
     pip install --no-cache-dir git+https://github.com/InternLM/lmdeploy.git && \
     echo "lmdeploy installed from source") || \
    (echo "Trying specific lmdeploy version..." && \
     pip install --no-cache-dir "lmdeploy>=0.2.0,<0.3.0" && \
     echo "lmdeploy installed (specific version)") || \
    (echo "ERROR: Failed to install lmdeploy with all methods" && \
     echo "TTS will use transformers backend (slower)" && \
     exit 0); \
    pip cache purge; \
fi'

# Ensure soprano-tts is installed (should be from Poetry, but verify)
RUN sh -c 'if ! python3 -c "import soprano_tts" 2>/dev/null; then \
    echo "soprano-tts not installed, installing..."; \
    pip install --no-cache-dir soprano-tts || \
    echo "Warning: soprano-tts installation failed - TTS will not be available"; \
fi'

# Verify lmdeploy installation
RUN python3 -c "import lmdeploy; print('✅ lmdeploy successfully installed (version:', getattr(lmdeploy, '__version__', 'unknown'), ')')" || \
    (python3 -c "print('⚠️  WARNING: lmdeploy is not installed - soprano-tts will use transformers backend (slower)')" && exit 0)

FROM python:3.12-slim
WORKDIR /app

# Install runtime dependencies
# For GPU support, nvidia-container-toolkit must be installed on the host
RUN apt-get update && apt-get install -y \
    libpq-dev \
    curl \
    libatomic1 \
    postgresql-client \
    && rm -rf /var/lib/apt/lists/*
COPY --from=builder /usr/local/lib/python3.12/site-packages /usr/local/lib/python3.12/site-packages
COPY --from=builder /usr/local/bin /usr/local/bin
COPY . .
RUN prisma generate
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --start-period=5s --retries=3 \
  CMD curl -f http://localhost:8000/health || exit 1
CMD ["uvicorn", "src.main:app", "--host", "0.0.0.0", "--port", "8000"]