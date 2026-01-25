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


############################
# Builder
############################
FROM python:3.12-slim AS builder
WORKDIR /app

ARG PYTORCH_VERSION=cpu
ARG CUDA_VERSION=cu121

# System deps
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    curl \
    git \
    cmake \
    ninja-build \
    rapidjson-dev \
    && rm -rf /var/lib/apt/lists/*

# Install Poetry
RUN curl -sSL https://install.python-poetry.org | python3 -
ENV PATH="/root/.local/bin:$PATH"

# Disable venvs cleanly
RUN poetry config virtualenvs.create false

# Copy dependency files
COPY pyproject.toml poetry.lock ./

# Install app deps (NO torch, NO lmdeploy here)
RUN poetry install --no-root --no-interaction --without dev

# ---- PyTorch (explicit, controlled) ----
RUN if [ "$PYTORCH_VERSION" = "cuda" ]; then \
      pip install --no-cache-dir torch torchvision torchaudio \
        --index-url https://download.pytorch.org/whl/${CUDA_VERSION}; \
    else \
      pip install --no-cache-dir torch torchvision torchaudio \
        --index-url https://download.pytorch.org/whl/cpu; \
    fi

# ---- Optional lmdeploy ----
RUN pip install --no-cache-dir lmdeploy || \
    echo "⚠️ lmdeploy not installed, soprano will use transformers backend"

# Sanity checks
RUN python - <<EOF
import fastapi, prisma, torch
print("✅ Core deps OK")
try:
    import soprano_tts
    print("✅ soprano-tts OK")
except ImportError:
    print("⚠️ soprano-tts missing")
EOF

############################
# Runtime
############################
FROM python:3.12-slim
WORKDIR /app

# Runtime libs only
RUN apt-get update && apt-get install -y --no-install-recommends \
    libpq-dev \
    curl \
    libatomic1 \
    postgresql-client \
    && rm -rf /var/lib/apt/lists/*

# Copy python env
COPY --from=builder /usr/local/lib/python3.12/site-packages \
                     /usr/local/lib/python3.12/site-packages
COPY --from=builder /usr/local/bin /usr/local/bin

# Copy app
COPY . .

# Prisma client
RUN python -m prisma generate

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --retries=3 \
  CMD curl -f http://localhost:8000/health || exit 1

CMD ["uvicorn", "src.main:app", "--host", "0.0.0.0", "--port", "8000"]
