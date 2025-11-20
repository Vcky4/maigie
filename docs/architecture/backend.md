# Backend (FastAPI) — Project Structure & Specs

```
backend/
  ├─ src/
  │   ├─ main.py                # FastAPI app factory
  │   ├─ routes/
  │   │   ├─ auth.py
  │   │   ├─ users.py
  │   │   ├─ courses.py
  │   │   ├─ goals.py
  │   │   ├─ resources.py
  │   │   ├─ schedule.py
  │   │   ├─ ai.py
  │   │   └─ realtime.py         # websocket endpoints
  │   ├─ services/              # business logic
  │   ├─ models/                # Pydantic schemas + ORM models
  │   ├─ db/                    # DB connection, migrations
  │   ├─ tasks/                 # background tasks (Celery/Dramatiq)
  │   ├─ ai_client/             # LLM and embeddings clients, prompt templates
  │   ├─ workers/               # async workers for indexing, recommendations
  │   └─ utils/
  └─ tests/
```

## API Design Principles

* REST for resources + a few RPC-like endpoints for AI actions. Use consistent versioning: `/api/v1/...`.
* All requests validated with Pydantic.
* Paginate list endpoints, support filters and full-text/semantic search.
* Rate-limit AI endpoints per-user.

---

# Core API Endpoints

## Auth

* `POST /api/v1/auth/register` — body: `{ email, password, name? }`
* `POST /api/v1/auth/login` — returns `access_token` (JWT), `refresh_token`
* `POST /api/v1/auth/refresh`
* `GET /api/v1/auth/me`

## Users

* `GET /api/v1/users/{id}`
* `PATCH /api/v1/users/{id}`

## Courses

* `GET /api/v1/courses` — list (filters: enrolled, created)
* `POST /api/v1/courses` — create course
* `GET /api/v1/courses/{id}` — details (modules)
* `POST /api/v1/courses/{id}/enroll`

## Goals

* `GET /api/v1/goals`
* `POST /api/v1/goals`
* `PATCH /api/v1/goals/{id}`
* `POST /api/v1/goals/{id}/progress` — record progress event

## Resources

* `GET /api/v1/resources` — (search, filter by type)
* `POST /api/v1/resources` — add manual resource or add AI-recommended resource
* `POST /api/v1/resources/recommend` — body: `{ context: {courses, goals, recentActivity} }` → returns recommended resources (uses embeddings + LLM)

## Schedule & Reminders

* `GET /api/v1/schedule?date=YYYY-MM-DD`
* `POST /api/v1/schedule` — create time block (title, start, end, recurring)
* `POST /api/v1/reminders` — schedule notification

## AI Assistant

* `POST /api/v1/ai/chat` — body: `{ message, contextRefs?: [noteIds, courseIds], voice?: false }` → returns assistant message
* `POST /api/v1/ai/voice-session` — start voice channel (websocket or WebRTC signaling)
* `POST /api/v1/ai/summary` — summarize a note or course
* `POST /api/v1/ai/create-plan` — `{ goalId | courseId }` → returns study plan with schedule suggestions

## Realtime

* `GET /api/v1/realtime/ws` — websocket for chat + live progress updates

---

# API Specification / OpenAPI

* FastAPI will auto-generate an OpenAPI spec; publish and generate TypeScript client for frontend.
* Use strict schema validation for all endpoints and return standardized error object `{ code, message, details? }`.

