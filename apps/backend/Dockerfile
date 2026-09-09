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
RUN apt-get update && apt-get install -y build-essential curl && rm -rf /var/lib/apt/lists/*
# Install Poetry from PyPI into its own venv. Avoids install.python-poetry.org,
# which is flaky from CI runners, and keeps Poetry out of the app's site-packages.
ENV POETRY_VERSION=2.4.1
RUN python3 -m venv /opt/poetry \
  && /opt/poetry/bin/pip install --no-cache-dir --retries 5 --timeout 60 "poetry==${POETRY_VERSION}"
ENV PATH="/opt/poetry/bin:$PATH"
COPY pyproject.toml poetry.lock ./
RUN poetry config virtualenvs.create false && poetry install --no-root --no-interaction --no-ansi

FROM python:3.12-slim
WORKDIR /app
RUN apt-get update && apt-get install -y libpq-dev curl libatomic1 postgresql-client fonts-dejavu-core && rm -rf /var/lib/apt/lists/*
COPY --from=builder /usr/local/lib/python3.12/site-packages /usr/local/lib/python3.12/site-packages
COPY --from=builder /usr/local/bin /usr/local/bin
COPY . .
EXPOSE 8000
HEALTHCHECK --interval=10s --timeout=5s --start-period=30s --retries=3 \
  CMD curl -f http://localhost:8000/health || exit 1
CMD ["sh", "-c", "python -m src.init_schema 2>&1 || echo '⚠ create_all skipped'; alembic upgrade head 2>&1 || echo '⚠ alembic upgrade skipped'; exec uvicorn src.main:app --host 0.0.0.0 --port 8000"]