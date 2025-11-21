# Backend (FastAPI)

FastAPI backend application for Maigie.

## Setup

1. Install dependencies:
```bash
poetry install
```

2. Run the development server:
```bash
nx serve backend
```

Or directly:
```bash
cd apps/backend
uvicorn src.main:app --reload
```

## Project Structure

- `src/main.py` - FastAPI app factory
- `src/routes/` - API route handlers
- `src/services/` - Business logic
- `src/models/` - Pydantic schemas and ORM models
- `src/db/` - Database connection and migrations
- `src/tasks/` - Background tasks (Celery/Dramatiq)
- `src/ai_client/` - LLM and embeddings clients
- `src/workers/` - Async workers
- `src/utils/` - Utility functions
- `tests/` - Test files

