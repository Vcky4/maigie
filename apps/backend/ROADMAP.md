# Maigie Backend — Feature Roadmap

> Maps The Maigie Book vision to concrete engineering work.
> Priority: P0 = must-have for launch, P1 = next quarter, P2 = future.

---

## Current State (Post-Refactor)

Architecture is complete. 9 domains, clean separation, domain events, multi-file schema.
Product capabilities are ~40% of the book's vision.

---

## Phase A: Foundation (Run the App)

| Task | Status | Notes |
|------|--------|-------|
| Verify `prisma generate` with schema folder | ❌ | Test locally with DB |
| Run `uvicorn src.app:app` successfully | ❌ | Fix any import errors in *_impl.py files |
| Add test suite skeleton (pytest) | ❌ | Unit + integration structure |
| CI pipeline for new structure | ❌ | Update GitHub Actions |

---

## Phase B: Tests (P0)

| Domain | Unit Tests | Integration Tests |
|--------|-----------|-------------------|
| Identity (auth, login, signup) | ❌ | ❌ |
| Billing (checkout, credits, webhooks) | ❌ | ❌ |
| Knowledge (courses, CRUD, AI gen) | ❌ | ❌ |
| Personal Learning (notes, exam prep) | ❌ | ❌ |
| Learning Spaces (membership, seats) | ❌ | ❌ |
| Intelligence (chat, memory) | ❌ | ❌ |
| Progress (goals, streaks, spaced rep) | ❌ | ❌ |
| Shared (auth, events, cache) | ❌ | ❌ |

---

## Phase C: Missing Features by Book Chapter

### P0 — Core Experience (Launch Blockers)

| Feature | Book Chapter | Domain | Endpoints Needed |
|---------|-------------|--------|-----------------|
| **Personal Learning Home** | Ch. 7 | Personal Learning | `GET /learning/home` — personalized greeting, today's focus, progress summary, recommended next step |
| **Learning Space Home** | Ch. 8 | Learning Spaces | `GET /spaces/{id}/home` — community activity, learner-specific view, upcoming sessions |
| **Classroom Home** | Ch. 9 | Classrooms | `GET /classrooms/{id}/home` — today's objective, active discussions, pending assignments |
| **Proactive Notifications** | Ch. 32 | Intelligence | Celery beat tasks that generate smart notifications (revision reminders, streak warnings, encouragement) |
| **Topic completion → spaced rep** | Ch. 12 | Progress | Event listener on `topic.completed` that creates ReviewSchedule (wired but not executing) |

### P1 — Intelligence Capabilities

| Feature | Book Chapter | Domain | What to Build |
|---------|-------------|--------|---------------|
| **Proactive AI** | Ch. 11, 22 | Intelligence | Scheduled tasks that observe patterns and take action: recommend revision before exams, notice declining engagement, suggest collaboration |
| **Prescriptive Memory** | Ch. 13, 23 | Intelligence | Memory that shapes recommendations: "Based on your history, today focus on X" — not just "you studied Y yesterday" |
| **Behaviour Understanding** | Ch. 14 | Progress | Track learning patterns: when learner studies best, session duration trends, what causes dropout. Feed into Intelligence |
| **Educator Dashboard** | Ch. 17 | Learning Spaces | `GET /spaces/{id}/insights` — struggling learners, curriculum gaps, effective practices, suggested improvements |
| **Recommendations Engine** | Ch. 33 | Intelligence | `GET /intelligence/discover` — proactive discovery: resources, collaborators, topics connected to current learning |
| **Intelligent Notifications** | Ch. 32 | Intelligence | ML-based notification timing: "right moment" delivery based on user patterns, not fixed schedules |

### P2 — Institutional & Advanced

| Feature | Book Chapter | Domain | What to Build |
|---------|-------------|--------|---------------|
| **Institutional Intelligence** | Ch. 26 | Admin + Intelligence | Cross-space analytics: department performance, teaching practice effectiveness, emerging gaps |
| **Autonomous Learning** | Ch. 27 | Intelligence | Self-improving environment: courses get better each semester, communities self-organize, AI adapts without manual config |
| **The Future University** | Ch. 55 | Learning Spaces + Admin | Multi-space coordination, alumni networks, cross-institution collaboration |
| **Learning Agents** | Ch. 25 | Intelligence | Multiple specialized agents (Revision Agent, Planning Agent, Classroom Agent) coordinating as one Intelligence |
| **Support Network** | Ch. 21 | New domain | Parents/guardians/mentors/sponsors: controlled visibility into learner progress |
| **Course Evolution** | Ch. 10 | Knowledge | Courses improve over time: AI suggests new resources, identifies weak spots, recommends restructuring based on learner outcomes |
| **Community Culture** | Ch. 18 | Learning Spaces | Contribution tracking, peer recognition, healthy community metrics, moderation intelligence |

---

## Phase D: Client Migration

| Task | Status |
|------|--------|
| Map old endpoints → new endpoints | ❌ |
| Update web client (maigie-client) | ❌ |
| Update mobile client (maigie-mobile) | ❌ |
| Run DB table rename migration | ❌ |
| Update Prisma schema to use new names | ❌ |
| Run `prisma generate` | ❌ |
| Deploy and verify | ❌ |

---

## Quick Reference: What to Build per Endpoint

### Personal Learning Home (P0)
```
GET /api/v1/learning/home
Response:
{
  greeting: "Good morning, Victor.",
  todaysFocus: { courseTitle, topicTitle, reason },
  progress: { streak, weeklyMinutes, topicsCompleted },
  upcomingSchedule: [{ title, startAt }],
  dueReviews: [{ topicTitle, dueAt }],
  recommendations: [{ type, title, reason }]
}
```

### Proactive Intelligence (P1)
```
Celery Beat Schedule:
- Every 6h: check_declining_engagement() → send nudge
- Every morning: prepare_daily_plan() → push notification
- Before exams: suggest_revision_sessions() → smart notification
- Weekly: generate_weekly_insights() → email
```

### Educator Dashboard (P1)
```
GET /api/v1/spaces/{id}/insights
Response:
{
  atRiskLearners: [{ userId, name, reason, suggestedAction }],
  classroomHealth: [{ classroomId, engagement, trend }],
  curriculumGaps: [{ topicTitle, failRate, suggestion }],
  bestPractices: [{ practice, evidence }]
}
```

---

## Running the App Today

```bash
# Prerequisites
- Python 3.11+
- PostgreSQL (local or Neon)
- Redis (local or Docker: docker run -p 6379:6379 redis)

# Setup
cd apps/backend
.\setup-dev.ps1          # Installs Poetry + dependencies
cp .env.example .env     # Edit with your DB/Redis URLs

# Database
poetry run prisma generate --schema prisma/schema
poetry run prisma db push --schema prisma/schema  # For dev (creates tables)

# Run
poetry run uvicorn src.app:app --reload --port 8000

# Docs
open http://localhost:8000/redoc
open http://localhost:8000/docs

# Workers (separate terminal)
poetry run celery -A src.core.celery_app:celery_app worker -Q default,heavy --loglevel=info
```

---

*This roadmap will be updated as features are implemented.*
