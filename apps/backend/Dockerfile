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
# Build arguments
# ----------------
ARG PYTORCH_VERSION=cpu
ARG CUDA_VERSION=cu121

# ----------------
# System deps (minimal)
# ----------------
RUN apt-get update && apt-get install -y \
    ffmpeg \
    git \
    curl \
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
COPY pyproject.toml poetry.lock ./

# ----------------
# Install PyTorch based on build args
# ----------------
RUN if [ "$PYTORCH_VERSION" = "cuda" ]; then \
        pip install --no-cache-dir \
            torch \
            --index-url https://download.pytorch.org/whl/${CUDA_VERSION}; \
    else \
        pip install --no-cache-dir \
            torch==2.1.0+cpu \
            --index-url https://download.pytorch.org/whl/cpu; \
    fi

# ----------------
# Install app deps and clean up
# ----------------
RUN poetry install --no-root --without dev && \
    # Clean up pip cache
    pip cache purge || true && \
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
    rm -rf /tmp/* /var/tmp/* 2>/dev/null || true

# ----------------
# Copy app
# ----------------
COPY . .

# ----------------
# Run
# ----------------
EXPOSE 8000
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
