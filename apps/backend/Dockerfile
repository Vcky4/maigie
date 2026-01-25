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
# CUDA base image
# ================================
FROM nvidia/cuda:12.1.1-cudnn8-runtime-ubuntu22.04 AS app

# ----------------
# System deps
# ----------------
RUN apt-get update && apt-get install -y \
    python3.11 \
    python3.11-distutils \
    python3-pip \
    build-essential \
    ffmpeg \
    git \
    curl \
 && rm -rf /var/lib/apt/lists/*

RUN ln -sf /usr/bin/python3.11 /usr/bin/python

# ----------------
# Python tooling
# ----------------
RUN pip install --upgrade pip poetry
RUN poetry config virtualenvs.create false

WORKDIR /app

# ----------------
# Copy dependency files
# ----------------
COPY pyproject.toml poetry.lock ./

# ----------------
# Install torch FIRST (CUDA)
# ----------------
RUN pip install --no-cache-dir \
    torch==2.1.0 \
    --index-url https://download.pytorch.org/whl/cu121

# ----------------
# Install app dependencies
# ----------------
RUN poetry install --no-root --no-interaction --without dev

# ----------------
# Copy app code
# ----------------
COPY . .

# ----------------
# Runtime
# ----------------
EXPOSE 8000

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
