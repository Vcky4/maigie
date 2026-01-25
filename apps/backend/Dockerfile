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
RUN apt-get update && apt-get install -y \
    ffmpeg \
    git \
    curl \
 && rm -rf /var/lib/apt/lists/*

# ----------------
# Python tooling
# ----------------
RUN pip install --no-cache-dir --upgrade pip poetry
RUN poetry config virtualenvs.create false

WORKDIR /app

# ----------------
# Copy deps first (cache-friendly)
# ----------------
COPY pyproject.toml poetry.lock ./

# ----------------
# Install torch (CPU ONLY)
# ----------------
RUN pip install --no-cache-dir \
    torch==2.1.0+cpu \
    --index-url https://download.pytorch.org/whl/cpu

# ----------------
# Prevent heavy builds
# ----------------
ENV PIP_NO_CACHE_DIR=1
ENV POETRY_NO_INTERACTION=1

# ----------------
# Install app deps
# ----------------
RUN poetry install --no-root --without dev

# ----------------
# Copy app
# ----------------
COPY . .

# ----------------
# Run
# ----------------
EXPOSE 8000
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
