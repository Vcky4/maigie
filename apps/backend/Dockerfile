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

# Install PyTorch first (required by lmdeploy)
# CUDA version works on both GPU and CPU (falls back to CPU if GPU unavailable)
RUN if [ "$PYTORCH_VERSION" = "cuda" ]; then \
        echo "Installing CUDA-enabled PyTorch (CUDA ${CUDA_VERSION})..." && \
        pip install --no-cache-dir torch torchvision torchaudio --index-url https://download.pytorch.org/whl/${CUDA_VERSION}; \
    else \
        echo "Installing CPU-only PyTorch..." && \
        pip install --no-cache-dir torch torchvision torchaudio --index-url https://download.pytorch.org/whl/cpu; \
    fi

# Install all dependencies via Poetry (including soprano-tts, but lmdeploy may fail)
RUN poetry config virtualenvs.create false && poetry install --no-root --no-interaction --no-ansi || true

# Install lmdeploy - required for soprano-tts performance
# Try multiple installation methods to ensure it works
RUN set -e; \
    if ! python3 -c "import lmdeploy" 2>/dev/null; then \
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
    fi

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