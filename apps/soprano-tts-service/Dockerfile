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

# ================================
# Base (small + stable)
# ================================
FROM python:3.11-slim

# ----------------  
# System deps (minimal)
# ----------------
ENV DEBIAN_FRONTEND=noninteractive
RUN apt-get update && apt-get install -y --no-install-recommends \
    ffmpeg \
    git \
    curl \
    build-essential \
 && rm -rf /var/lib/apt/lists/* \
 && apt-get clean

# ----------------  
# Python tooling
# ----------------
RUN pip install --no-cache-dir --upgrade pip poetry
RUN poetry config virtualenvs.create false

WORKDIR /app

# ----------------  
# Prevent heavy builds
# ----------------
ENV PIP_NO_CACHE_DIR=1
ENV POETRY_NO_INTERACTION=1

# ----------------  
# Copy deps first (cache-friendly)
# ----------------
COPY pyproject.toml ./

# ----------------  
# Install app deps with poetry
# ----------------
RUN poetry install --no-root --without dev && \
    # Clean up poetry + pip caches (best-effort)
    (poetry cache clear pypi --all -n || true) && \
    (pip cache purge || true) && \
    rm -rf /root/.cache/pypoetry /root/.cache/pip /tmp/* /var/tmp/* 2>/dev/null || true

# ----------------  
# Copy app
# ----------------
COPY . .

# ----------------  
# Generate gRPC code from proto files
# ----------------
RUN python -m grpc_tools.protoc \
    --proto_path=src/proto \
    --python_out=src/proto \
    --grpc_python_out=src/proto \
    src/proto/tts.proto

# ----------------  
# Final cleanup to reduce image size
# ----------------
RUN (pip cache purge || true) && \
    # Remove Python cache files (safe to remove)
    find /usr/local/lib/python3.11/site-packages -type d -name "__pycache__" -exec rm -rf {} + 2>/dev/null || true && \
    find /usr/local/lib/python3.11/site-packages -type f -name "*.pyc" -delete 2>/dev/null || true && \
    find /usr/local/lib/python3.11/site-packages -type f -name "*.pyo" -delete 2>/dev/null || true && \
    # Remove test directories (not needed in production)
    find /usr/local/lib/python3.11/site-packages -type d -name "tests" -exec rm -rf {} + 2>/dev/null || true && \
    find /usr/local/lib/python3.11/site-packages -type d -name "test" -exec rm -rf {} + 2>/dev/null || true && \
    find /usr/local/lib/python3.11/site-packages -type d -name "*test*" -exec rm -rf {} + 2>/dev/null || true && \
    # Remove documentation files (reduce size)
    find /usr/local/lib/python3.11/site-packages -type f -name "*.md" -delete 2>/dev/null || true && \
    find /usr/local/lib/python3.11/site-packages -type f -name "LICENSE*" -delete 2>/dev/null || true && \
    find /usr/local/lib/python3.11/site-packages -type f -name "CHANGELOG*" -delete 2>/dev/null || true && \
    # Clean up any temporary build artifacts
    rm -rf /tmp/* /var/tmp/* ~/.cache 2>/dev/null || true && \
    # Show disk usage for debugging
    df -h || true

# ----------------  
# Run
# ----------------
EXPOSE 50051
CMD ["python", "-m", "src.main"]
