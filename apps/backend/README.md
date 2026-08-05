# Backend (FastAPI)

FastAPI backend for Maigie — the AI-powered academic operating system.

## Architecture

This backend follows **domain-driven design**. See [`ARCHITECTURE.md`](./ARCHITECTURE.md) for the full specification.

```
src/
├── app.py                    # FastAPI application factory
├── config.py                 # Pydantic settings
├── shared/                   # Cross-cutting infrastructure
│   ├── auth/                 # JWT, dependencies, role checks
│   ├── database/             # SQLAlchemy async engine + session
│   ├── middleware/           # Logging, security headers
│   ├── exceptions/           # Unified error handling
│   └── infrastructure/       # Email, push, storage, WebSocket
├── domains/                  # Bounded contexts
│   ├── identity/             # Auth, users, profiles
│   ├── personal_learning/    # Notes, flashcards, study mode, exams
│   ├── knowledge/            # Courses, resources, curriculum
│   ├── learning_spaces/      # Collaborative environments
│   ├── classrooms/           # Structured learning within spaces
│   ├── intelligence/         # AI layer (reasoning, memory, planning, skills)
│   ├── progress/             # Analytics, streaks, goals, schedules
│   ├── billing/              # Payments, subscriptions, credits
│   └── admin/                # Platform administration
├── workers/                  # Celery background tasks
└── integrations/             # External service adapters (ElevenLabs, Pinecone, etc.)
```

## Quick Start

### Prerequisites

- Python 3.12+
- PostgreSQL running locally
- Poetry installed

### Setup

```bash
cd apps/backend

# Install dependencies
poetry install

# Copy env vars
cp .env.example .env
# Edit .env — set DATABASE_URL at minimum

# Run database migrations
poetry run alembic upgrade head

# Start the dev server
bash scripts/serve.sh
```

The server starts at `http://localhost:8000`. API docs at `/docs`.

### Background Workers (optional)

```bash
# Celery worker (processes background tasks)
bash scripts/start-worker.sh

# Celery Beat (scheduled periodic tasks)
bash scripts/start-beat.sh
```

## Code Style

- Formatter: **Black** (line-length 100)
- Linter: **Ruff**

```bash
poetry run black src tests
poetry run ruff check src
```

## Testing

```bash
poetry run pytest
```

## API Contract

This repository owns the canonical OpenAPI schema at [`openapi.json`](./openapi.json).
It is exported directly from the application factory, so no running server is required:

```bash
poetry run python scripts/export_openapi.py           # regenerate and write
poetry run python scripts/export_openapi.py --check   # fail if out of date
```

Regenerate and commit `openapi.json` whenever a request or response contract
changes. CI runs the `--check` mode, so contract drift fails the build.

Client repositories generate their own TypeScript types from this schema and own
that tooling; this repository stays Python-only.

## Key Endpoints

| Endpoint | Description |
|----------|-------------|
| `POST /api/v1/auth/login` | Login |
| `POST /api/v1/auth/register` | Register |
| `GET /api/v1/learning/home` | Learning home feed |
| `GET /api/v1/learning/dashboard` | Learn dashboard read model |
| `GET /api/v1/learning/notes` | List notes |
| `WS /api/v1/intelligence/ws` | AI chat WebSocket |
| `GET /docs` | Swagger UI |

## Environment Variables

See `.env.example` for all available configuration. Key settings:

- `DATABASE_URL` — PostgreSQL connection string (required)
- `GEMINI_API_KEY` — Google Gemini API key (required for AI)
- `SECRET_KEY` — JWT signing key
- `REDIS_URL` — Redis for caching and Celery broker
