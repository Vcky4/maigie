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

RUN apt-get update && apt-get install -y build-essential curl && rm -rf /var/lib/apt/lists/*
RUN curl -sSL https://install.python-poetry.org | python3 -
ENV PATH="/root/.local/bin:$PATH"
COPY pyproject.toml poetry.lock ./
RUN poetry config virtualenvs.create false && poetry install --no-root --no-interaction --no-ansi

# Install PyTorch based on build argument
# CUDA version works on both GPU and CPU (falls back to CPU if GPU unavailable)
# CPU version is smaller but GPU-only
RUN if [ "$PYTORCH_VERSION" = "cuda" ]; then \
        echo "Installing CUDA-enabled PyTorch (CUDA ${CUDA_VERSION})..." && \
        pip install --no-cache-dir torch torchvision torchaudio --index-url https://download.pytorch.org/whl/${CUDA_VERSION}; \
    else \
        echo "Installing CPU-only PyTorch..." && \
        pip install --no-cache-dir torch torchvision torchaudio --index-url https://download.pytorch.org/whl/cpu; \
    fi

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