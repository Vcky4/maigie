# ==================================
# Stage 1: The Builder (Heavy Lifting)
# ==================================
FROM python:3.12-slim AS builder

WORKDIR /app

# 1. Install system tools needed for building Python packages
RUN apt-get update && apt-get install -y \
    build-essential \
    curl \
    && rm -rf /var/lib/apt/lists/*

# 2. Install Poetry
RUN curl -sSL https://install.python-poetry.org | python3 -
ENV PATH="/root/.local/bin:$PATH"

# 3. Copy only dependency files first
COPY pyproject.toml poetry.lock ./

# 4. Install dependencies
RUN poetry config virtualenvs.create false \
    && poetry install --no-root --no-interaction --no-ansi

# ==================================
# Stage 2: The Final Runtime (Lightweight)
# ==================================
FROM python:3.12-slim

WORKDIR /app

# 1. Install minimal runtime libraries
# ADDED: libatomic1 (Required for Prisma/Node engine)
RUN apt-get update && apt-get install -y \
    libpq-dev \
    curl \
    libatomic1 \
    && rm -rf /var/lib/apt/lists/*

# 2. Copy the installed packages from the Builder stage
COPY --from=builder /usr/local/lib/python3.12/site-packages /usr/local/lib/python3.12/site-packages
COPY --from=builder /usr/local/bin /usr/local/bin

# 3. Copy your actual application code
COPY . .

# 4. Generate Prisma Client
RUN prisma generate

# 5. Expose the port
EXPOSE 8000

# 6. Healthcheck
HEALTHCHECK --interval=30s --timeout=5s --start-period=5s --retries=3 \
  CMD curl -f http://localhost:8000/health || exit 1

# 7. Start the server
CMD ["uvicorn", "src.main:app", "--host", "0.0.0.0", "--port", "8000"]