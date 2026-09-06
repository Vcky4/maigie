# Maigie Backend Architecture

> This document defines the engineering architecture of the Maigie backend.
> It is the source of truth for how the codebase is structured, why decisions were made,
> and how the domain model maps to code.
>
> Every engineer should read this before contributing.

---

## Philosophy

The backend architecture mirrors the learning environment described in The Maigie Book.

- The **domain model** uses the same language as the product (Learning Spaces, Classrooms, Knowledge, Intelligence, Progress).
- The **code structure** follows domain-driven design — each bounded context owns its models, routes, services, and data access.
- **Intelligence is infrastructure**, not a feature — it strengthens every domain without being coupled to any.
- **Shared concerns** (auth, database, events, middleware) live in a dedicated layer, never inside domains.

---

## Directory Structure

```
apps/backend/src/
│
├── app.py                              # FastAPI application factory
├── config.py                           # Pydantic settings (all env vars)
│
├── shared/                             # Cross-cutting infrastructure
│   ├── auth/                           # JWT, dependencies, role checks
│   │   ├── __init__.py
│   │   ├── dependencies.py             # CurrentUser, require_role(), etc.
│   │   ├── jwt.py                      # Token creation/validation
│   │   └── oauth.py                    # Google OAuth, OAuth 2.1 provider
│   │
│   ├── database/                       # SQLAlchemy async engine
│   │   ├── __init__.py
│   │   └── client.py                   # connect_db(), disconnect_db(), get_db()
│   │
│   ├── middleware/                      # HTTP middleware
│   │   ├── __init__.py
│   │   ├── logging.py                  # Structured request logging
│   │   └── security.py                 # Security headers, HSTS
│   │
│   ├── exceptions/                     # Unified error handling
│   │   ├── __init__.py
│   │   ├── base.py                     # MaigieError, AppException hierarchy
│   │   └── handlers.py                 # FastAPI exception handlers
│   │
│   ├── events/                         # Domain event bus
│   │   ├── __init__.py
│   │   ├── bus.py                      # EventBus (in-process pub/sub)
│   │   └── types.py                    # Base event classes
│   │
│   └── infrastructure/                 # External service connectors
│       ├── __init__.py
│       ├── redis.py                    # Redis client (cache + pub/sub)
│       ├── storage.py                  # BunnyCDN / S3 file storage
│       └── http.py                     # Shared httpx client
│
├── domains/                            # Bounded contexts (the heart)
│   │
│   ├── identity/                       # Authentication, users, profiles
│   │   ├── __init__.py
│   │   ├── models.py                   # Pydantic request/response schemas
│   │   ├── routes.py                   # /api/v1/auth/*, /api/v1/users/*
│   │   ├── services.py                # Auth logic, user management
│   │   ├── repository.py              # SQLAlchemy queries for User, Preferences
│   │   └── events.py                  # UserRegistered, UserOnboarded, etc.
│   │
│   ├── personal_learning/             # The learner's private environment
│   │   ├── __init__.py
│   │   ├── models.py
│   │   ├── routes.py                   # /api/v1/learning/*
│   │   ├── services/
│   │   │   ├── __init__.py
│   │   │   ├── note_service.py
│   │   │   ├── exam_prep_service.py
│   │   │   ├── document_service.py
│   │   │   └── study_mode_service.py
│   │   └── repository.py
│   │
│   ├── knowledge/                      # Courses, resources, curriculum
│   │   ├── __init__.py
│   │   ├── models.py
│   │   ├── routes.py                   # /api/v1/knowledge/*
│   │   ├── services/
│   │   │   ├── __init__.py
│   │   │   ├── course_service.py
│   │   │   ├── resource_service.py
│   │   │   ├── resource_bank_service.py
│   │   │   └── embedding_service.py
│   │   ├── repository.py
│   │   └── events.py                  # CourseCreated, ResourceAdded, etc.
│   │
│   ├── learning_spaces/               # Collaborative environments (was "Circles")
│   │   ├── __init__.py
│   │   ├── models.py
│   │   ├── routes.py                   # /api/v1/spaces/*
│   │   ├── services/
│   │   │   ├── __init__.py
│   │   │   ├── space_service.py
│   │   │   ├── membership_service.py
│   │   │   ├── seat_service.py
│   │   │   └── knowledge_base_service.py
│   │   ├── repository.py
│   │   └── events.py                  # SpaceCreated, MemberJoined, etc.
│   │
│   ├── classrooms/                    # Structured learning within spaces
│   │   ├── __init__.py
│   │   ├── models.py
│   │   ├── routes.py                   # /api/v1/classrooms/*
│   │   ├── services/
│   │   │   ├── __init__.py
│   │   │   ├── classroom_service.py
│   │   │   ├── session_service.py
│   │   │   └── discussion_service.py
│   │   └── repository.py
│   │
│   ├── intelligence/                  # The Intelligence Layer
│   │   ├── __init__.py
│   │   ├── routes.py                   # /api/v1/intelligence/* (chat, voice)
│   │   ├── models.py
│   │   ├── observation/               # Event listeners, activity tracking
│   │   │   ├── __init__.py
│   │   │   └── tracker.py
│   │   ├── memory/                    # Conversation summaries, user facts
│   │   │   ├── __init__.py
│   │   │   ├── memory_service.py
│   │   │   └── user_memory_service.py
│   │   ├── reasoning/                 # LLM orchestration
│   │   │   ├── __init__.py
│   │   │   ├── llm/                   # Multi-provider adapters
│   │   │   │   ├── __init__.py
│   │   │   │   ├── router.py
│   │   │   │   ├── base_adapter.py
│   │   │   │   ├── gemini.py
│   │   │   │   ├── openai.py
│   │   │   │   ├── anthropic.py
│   │   │   │   ├── circuit_breaker.py
│   │   │   │   ├── streaming.py
│   │   │   │   └── types.py
│   │   │   ├── prompts.py
│   │   │   └── rag_service.py
│   │   ├── planning/                  # Schedule generation, recommendations
│   │   │   ├── __init__.py
│   │   │   ├── planning_service.py
│   │   │   └── recommendation_service.py
│   │   ├── action/                    # Skills that execute plans
│   │   │   ├── __init__.py
│   │   │   ├── skill_registry.py
│   │   │   ├── skill_courses.py
│   │   │   ├── skill_scheduling.py
│   │   │   ├── skill_notes.py
│   │   │   ├── skill_goals.py
│   │   │   ├── skill_memory.py
│   │   │   ├── skill_resources.py
│   │   │   └── skill_documents.py
│   │   └── conversation/             # Chat session management
│   │       ├── __init__.py
│   │       ├── conversation_service.py
│   │       ├── greeting_service.py
│   │       └── websocket_handler.py
│   │
│   ├── progress/                      # Analytics, behaviour, achievements
│   │   ├── __init__.py
│   │   ├── models.py
│   │   ├── routes.py                   # /api/v1/progress/*
│   │   ├── services/
│   │   │   ├── __init__.py
│   │   │   ├── analytics_service.py
│   │   │   ├── streak_service.py
│   │   │   ├── achievement_service.py
│   │   │   ├── spaced_repetition_service.py
│   │   │   ├── goal_service.py
│   │   │   └── schedule_service.py
│   │   └── repository.py
│   │
│   ├── billing/                       # Payments, subscriptions, credits
│   │   ├── __init__.py
│   │   ├── models.py
│   │   ├── routes.py                   # /api/v1/billing/*
│   │   ├── services/
│   │   │   ├── __init__.py
│   │   │   ├── subscription_service.py
│   │   │   ├── credit_service.py
│   │   │   ├── stripe_service.py
│   │   │   ├── paystack_service.py
│   │   │   ├── google_play_service.py
│   │   │   └── referral_service.py
│   │   ├── webhooks.py                # Stripe/Paystack/Google Play webhook handlers
│   │   └── repository.py
│   │
│   └── admin/                         # Platform administration
│       ├── __init__.py
│       ├── models.py
│       ├── routes.py                   # /api/v1/admin/*
│       ├── services.py
│       └── repository.py
│
├── workers/                           # Celery background tasks
│   ├── __init__.py
│   ├── celery_app.py                  # Celery factory and configuration
│   ├── intelligence_tasks.py          # AI course gen, schedule gen, recommendations
│   ├── notification_tasks.py          # Email + push notifications
│   ├── progress_tasks.py             # Spaced repetition, streak updates
│   └── billing_tasks.py              # Subscription lifecycle, credit resets
│
└── integrations/                      # External service adapters
    ├── __init__.py
    ├── elevenlabs/                    # Voice synthesis
    │   ├── __init__.py
    │   └── client.py
    │                                  # (no vector database yet — see rag_service)
    │   ├── __init__.py
    │   └── client.py
    ├── firebase/                      # Push notifications (FCM)
    │   ├── __init__.py
    │   └── client.py
    ├── bunny_cdn/                     # File storage
    │   ├── __init__.py
    │   └── client.py
    ├── brevo/                         # CRM + transactional email
    │   ├── __init__.py
    │   └── client.py
    └── google_calendar/               # Calendar sync
        ├── __init__.py
        └── client.py
```

---

## Domain Model (Language)

These are the terms used throughout the codebase. They match The Maigie Book exactly.

### Core Concepts

| Concept | Description | DB Table |
|---------|-------------|----------|
| **User** | A single human identity. Authentication, profile, billing. | `User` |
| **LearningSpace** | A collaborative learning environment (was "Circle"). | `LearningSpace` |
| **SpaceMember** | A user's membership + role within a Learning Space. | `SpaceMember` |
| **Classroom** | A focused learning experience within a Space. | `Classroom` |
| **Course** | Structured, reusable knowledge (modules → topics). | `Course` |
| **Module** | A section within a Course. | `Module` |
| **Topic** | A single learning unit within a Module. | `Topic` |
| **Conversation** | A chat session between a user and Intelligence. | `Conversation` |
| **Message** | A single message within a Conversation. | `Message` |
| **StudyBlock** | A scheduled learning activity (was "ScheduleBlock"). | `StudyBlock` |
| **ReviewSchedule** | Spaced repetition schedule for a topic (was "ReviewItem"). | `ReviewSchedule` |
| **ExamPreparation** | A user's exam prep journey (was "ExamPrep"). | `ExamPreparation` |

### Roles (Contextual, not Global)

A User's role depends on **where** they are:

| Context | Roles |
|---------|-------|
| Platform | `user`, `staff` (with `staffRole`: SUPER_ADMIN, CONTENT_MANAGER) |
| Personal Learning | Always a **learner** (implicit, no role needed) |
| Learning Space | `OWNER`, `ADMIN`, `EDUCATOR`, `LEARNER` |
| Classroom | `LEAD_EDUCATOR`, `EDUCATOR`, `LEARNER` |

### Domain Boundaries

```
┌─────────────────────────────────────────────────────────────────────┐
│                         SHARED INFRASTRUCTURE                        │
│   auth / database / middleware / exceptions / events / redis         │
└─────────────────────┬───────────────────────────────────────────────┘
                      │
    ┌─────────────────┼─────────────────────────────────────────┐
    │                 │            DOMAINS                        │
    │                 ▼                                           │
    │  ┌──────────┐  ┌─────────────────┐  ┌────────────────┐   │
    │  │ Identity │  │ Personal Learn. │  │   Knowledge    │   │
    │  └──────────┘  └─────────────────┘  └────────────────┘   │
    │                                                            │
    │  ┌──────────────────┐  ┌────────────┐  ┌────────────┐   │
    │  │ Learning Spaces  │  │ Classrooms │  │  Progress   │   │
    │  └──────────────────┘  └────────────┘  └────────────┘   │
    │                                                            │
    │  ┌──────────────────┐  ┌────────────┐                    │
    │  │  Intelligence    │  │  Billing   │  ┌─────────┐       │
    │  │  (strengthens    │  └────────────┘  │  Admin  │       │
    │  │   all domains)   │                  └─────────┘       │
    │  └──────────────────┘                                    │
    └──────────────────────────────────────────────────────────┘
```

### Cross-Domain Communication

Domains communicate through **domain events**, never by importing each other's services directly.

Example: When a user completes a topic (Knowledge domain), it emits `TopicCompleted`. The Progress domain listens and updates the streak. The Intelligence domain listens and adjusts the next review schedule.

```python
# knowledge/services/course_service.py
from src.shared.events import emit

await emit("topic.completed", {"user_id": user_id, "topic_id": topic_id})

# progress/listeners.py
@listen("topic.completed")
async def update_streak(data):
    await streak_service.record_activity(data["user_id"])
```

---

## Data Access Pattern

Each domain owns a `repository.py` that encapsulates all SQLAlchemy queries for that domain.

```python
# domains/knowledge/repository.py

from src.shared.database import get_db

class KnowledgeRepository:
    def __init__(self, db=None):
        self.db = db or get_db()

    async def get_course(self, course_id: str, user_id: str):
        return await self.db.course.find_first(
            where={"id": course_id, "userId": user_id},
            include={"modules": {"include": {"topics": True}}}
        )

    async def create_course(self, data: dict):
        return await self.db.course.create(data=data)
```

Services call repositories. Routes call services. Never skip layers.

```
Route → Service → Repository → Prisma → PostgreSQL
```

---

## Intelligence Layer Architecture

The Intelligence domain follows the cognitive architecture from Chapter 50:

```
Observe → Remember → Reason → Plan → Act
```

| Capability | Package | Responsibility |
|-----------|---------|----------------|
| **Observation** | `intelligence/observation/` | Listen to domain events, track activity |
| **Memory** | `intelligence/memory/` | Conversation summaries, user facts, long-term context |
| **Reasoning** | `intelligence/reasoning/` | LLM routing, RAG, prompt engineering |
| **Planning** | `intelligence/planning/` | Schedule generation, learning recommendations |
| **Action** | `intelligence/action/` | Skills that modify the learning environment |
| **Conversation** | `intelligence/conversation/` | Chat session management, WebSocket handling |

---

## Naming Conventions

### Python

- **Files**: `snake_case.py`
- **Classes**: `PascalCase`
- **Functions/methods**: `snake_case`
- **Constants**: `UPPER_SNAKE_CASE`
- **Domain packages**: `snake_case` matching the concept

### API Routes

- **Base**: `/api/v1/{domain}/{resource}`
- **Examples**:
  - `POST /api/v1/auth/login`
  - `GET /api/v1/knowledge/courses`
  - `POST /api/v1/spaces/{space_id}/classrooms`
  - `GET /api/v1/progress/streaks`
  - `POST /api/v1/intelligence/conversations`

### Database

- **Tables**: `PascalCase` (Prisma convention)
- **Columns**: `camelCase` (Prisma convention)
- **Relations**: Named clearly (`spaceMemberships`, `conversations`, `studyBlocks`)

---

## Migration Strategy

### Approach: Clean Break + Data Migration

We do NOT use `@@map` to alias old names. We create new tables with correct names and migrate data.

### Migration Order

1. Create new schema alongside old (additive migration)
2. Write migration scripts that copy data from old tables to new
3. Verify data integrity
4. Switch application code to new schema
5. Drop old tables (after verification period)

### Production Safety

- All migrations run in transactions
- Rollback scripts accompany every migration
- Feature flags control which code path (old vs new) is active
- Blue-green deployment during cutover

---

## Dependencies Between Domains

Allowed dependencies (via events or shared interfaces):

```
identity ← (all domains need user context)
knowledge ← intelligence (courses, resources for context)
knowledge ← progress (topic completion)
learning_spaces ← classrooms (spaces contain classrooms)
intelligence → (observes all domains via events)
billing ← identity (user subscription status)
```

Forbidden:
- No domain imports another domain's services directly
- No domain accesses another domain's repository
- No circular dependencies

---

## Testing Strategy

```
tests/
├── unit/                    # Pure logic, mocked dependencies
│   ├── domains/
│   │   ├── identity/
│   │   ├── knowledge/
│   │   └── ...
│   └── shared/
├── integration/             # Real database, real Redis
│   ├── domains/
│   └── shared/
└── e2e/                     # Full API tests
    └── api/
```

---

## Future Considerations

- **Microservice extraction**: Each domain is already a bounded context. If scale demands it, any domain can become its own service with minimal refactoring.
- **Event sourcing**: The event bus pattern allows future migration to event sourcing if needed.
- **Multi-tenancy**: The Learning Space / Institution hierarchy naturally supports multi-tenant isolation.
- **API versioning**: If needed in the future, each domain can independently version its routes.

---

*This document was created as part of the Maigie backend refactor.*
*It reflects the architecture described in The Maigie Book and should be updated as the system evolves.*
