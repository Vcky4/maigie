# Personal Learning API — Swagger Manual Test Plan

This document provides step-by-step instructions for manually testing the Personal Learning API using Swagger UI at `http://localhost:8000/docs`.

---

## Prerequisites

### 1. Start the Server

```powershell
cd apps/backend

# Ensure dependencies are installed
poetry install

# Run database migrations (creates the 15 new personal learning tables)
poetry run alembic upgrade head

# Start the server
.\scripts\serve.ps1
# Or: .venv\Scripts\python.exe -m uvicorn src.app:app --reload --host 0.0.0.0 --port 8000
```
 
### 2. Required Services

| Service | How to Start | Verify |
|---------|-------------|--------|
| PostgreSQL | Local install or Docker: `docker run -p 5432:5432 -e POSTGRES_PASSWORD=postgres postgres` | `psql -h localhost -U postgres` |
| Redis | Docker: `docker run -p 6379:6379 redis` | `redis-cli ping` → PONG |

### 3. Environment Variables (`.env`)

Ensure these are set in `apps/backend/.env`:
```
DATABASE_URL=postgresql+asyncpg://postgres:postgres@localhost:5432/maigie
REDIS_URL=redis://localhost:6379/0
SECRET_KEY=your-secret-key-here
GEMINI_API_KEY=your-gemini-key    # Required for AI features
```

### 4. Open Swagger

Navigate to: **http://localhost:8000/docs**

All personal learning endpoints are grouped under the tag **"personal-learning"**.

---

## Authentication Setup

All endpoints (except document sharing) require a JWT token.

### Step 1: Create a Test User

**Endpoint:** `POST /api/v1/auth/register`

```json
{
  "email": "testlearner@example.com",
  "password": "TestPassword123!",
  "name": "Test Learner",
  "username": "testlearner"
}
```

### Step 2: Log In

**Endpoint:** `POST /api/v1/auth/login`

```json
{
  "email": "testlearner@example.com",
  "password": "TestPassword123!"
}
```

**Expected Response:**
```json
{
  "accessToken": "eyJhbGciOiJIUzI1NiIs...",
  "refreshToken": "...",
  "user": { "id": "...", "email": "testlearner@example.com" }
}
```

### Step 3: Authorize in Swagger

1. Copy the `accessToken` value from the login response
2. Click the **🔒 Authorize** button (top-right of Swagger UI)
3. In the "Value" field, paste: `Bearer eyJhbGciOiJIUzI1NiIs...`
4. Click **Authorize**, then **Close**

Now all subsequent requests will include the JWT token automatically.

---

## Test Flow (Execute in Order)

### Phase 1: Onboarding

#### Test 1.1 — Set Learning Purpose

**Endpoint:** `POST /api/v1/learning/onboarding/purpose`

**Request Body:**
```json
{
  "purpose": "exam_prep"
}
```

**Expected:** `201 Created`
```json
{
  "id": "...",
  "userId": "...",
  "purpose": "exam_prep",
  "subjects": null,
  "goalsText": null,
  "maturityDays": 0,
  "createdAt": "2026-07-16T..."
}
```

#### Test 1.2 — Set Subjects and Goals

**Endpoint:** `POST /api/v1/learning/onboarding/subjects`

**Request Body:**
```json
{
  "subjects": ["mathematics", "physics", "computer science"],
  "goals": "Prepare for university entrance exams in September"
}
```

**Expected:** `200 OK` — Profile updated with subjects and goals.

#### Test 1.3 — Get Profile

**Endpoint:** `GET /api/v1/learning/profile`

**Expected:** `200 OK` — Returns the full learning profile with purpose, subjects, goals.

---

### Phase 2: Personal Learning Home

#### Test 2.1 — Get Home (Onboarding State)

**Endpoint:** `GET /api/v1/learning/home`

**Expected:** `200 OK`
```json
{
  "greeting": "Good morning.",
  "todaysFocus": { "courseTitle": null, "topicTitle": "...", "reason": "..." },
  "progressSummary": { "currentStreak": 0, "weeklyMinutes": 0, "topicsCompletedThisWeek": 0 },
  "dueReviews": [],
  "scheduleBlocks": [],
  "recommendations": [
    { "type": "onboarding", "title": "Set your learning goals", "reason": "...", "actionData": {...} }
  ],
  "nextAction": { "type": "explore", "title": "Explore something new", "actionData": null },
  "reEngagement": null,
  "isOnboarding": true
}
```

**Verify:**
- `isOnboarding` is `true` (maturity ≤ 7 days)
- `recommendations` contains onboarding-oriented actions
- `nextAction` is never null
- `progressSummary` values are all non-negative

---

### Phase 3: Notes

#### Test 3.1 — Create a Note

**Endpoint:** `POST /api/v1/learning/notes`

**Request Body:**
```json
{
  "title": "Binary Search Trees",
  "content": "A binary search tree (BST) is a node-based binary tree data structure where the left subtree contains only nodes with keys less than the node's key, and the right subtree contains only nodes with keys greater than the node's key.",
  "tags": ["data-structures", "algorithms"]
}
```

**Expected:** `201 Created` with ID, timestamps, tags.

#### Test 3.2 — Create Second Note

**Endpoint:** `POST /api/v1/learning/notes`

```json
{
  "title": "Graph Traversal Algorithms",
  "content": "BFS and DFS are the two primary approaches to graph traversal. BFS uses a queue and explores level by level. DFS uses a stack and goes as deep as possible first.",
  "tags": ["algorithms", "graphs"]
}
```

#### Test 3.3 — List Notes

**Endpoint:** `GET /api/v1/learning/notes`

**Query params:** `page=1&size=10`

**Expected:** `200 OK`
```json
{
  "items": [...],
  "total": 2,
  "page": 1,
  "size": 10,
  "pages": 1
}
```

#### Test 3.4 — Search Notes

**Endpoint:** `GET /api/v1/learning/notes?search=binary`

**Expected:** Only the "Binary Search Trees" note returned.

#### Test 3.5 — Filter by Tag

**Endpoint:** `GET /api/v1/learning/notes?tag=graphs`

**Expected:** Only the "Graph Traversal" note returned.

#### Test 3.6 — Get Single Note

**Endpoint:** `GET /api/v1/learning/notes/{note_id}`

Use the `id` from Test 3.1 response.

**Expected:** `200 OK` — Full note with content, tags, attachments.

#### Test 3.7 — Update Note

**Endpoint:** `PATCH /api/v1/learning/notes/{note_id}`

```json
{
  "content": "Updated: A BST is a sorted binary tree with efficient search, insert, and delete operations (O(log n) average)."
}
```
 
**Expected:** `200 OK` — Only content changed, title preserved.

#### Test 3.8 — AI Summary (Requires GEMINI_API_KEY)

**Endpoint:** `POST /api/v1/learning/notes/{note_id}/summary`

**Expected:** `200 OK` — Note now has a `summary` field populated by AI.

#### Test 3.9 — Delete Note

**Endpoint:** `DELETE /api/v1/learning/notes/{note_id}`

Use the second note's ID.

**Expected:** `204 No Content`

#### Test 3.10 — Validation: Empty Title

**Endpoint:** `POST /api/v1/learning/notes`

```json
{
  "title": "",
  "content": "test"
}
```

**Expected:** `422 Unprocessable Entity`

---

### Phase 4: Flashcards

#### Test 4.1 — Create Flashcard

**Endpoint:** `POST /api/v1/learning/flashcards`

```json
{
  "front": "What is the time complexity of searching in a balanced BST?",
  "back": "O(log n)"
}
```

**Expected:** `201 Created`
```json
{
  "id": "...",
  "front": "What is the time complexity...",
  "back": "O(log n)",
  "intervalDays": 1,
  "repetitionCount": 0,
  "easeFactor": 2.5,
  "lapseCount": 0,
  "nextReviewAt": "..."
}
```

**Verify SM-2 initialization:** interval=1, repetition=0, ease=2.5, lapse=0.

#### Test 4.2 — Create More Flashcards

Create 2-3 more flashcards for variety.

#### Test 4.3 — Get Flashcard Stats

**Endpoint:** `GET /api/v1/learning/flashcards/stats`

**Expected:**
```json
{
  "total": 3,
  "dueToday": 0,
  "masteredCount": 0,
  "averageEaseFactor": 2.5
}
```

(Cards won't be due until tomorrow since interval=1 day)

#### Test 4.4 — Generate Flashcards from Note (AI)

**Endpoint:** `POST /api/v1/learning/flashcards/generate/note/{note_id}`

Use the BST note's ID.

**Expected:** `200 OK` — Array of generated flashcards.

#### Test 4.5 — Create a Deck

**Endpoint:** `POST /api/v1/learning/decks`

```json
{
  "title": "Data Structures",
  "description": "Core data structure concepts"
}
```
 
#### Test 4.6 — List Decks

**Endpoint:** `GET /api/v1/learning/decks`

**Expected:** The deck you just created.

---

### Phase 5: Preparations

#### Test 5.1 — Create Exam Preparation

**Endpoint:** `POST /api/v1/learning/preparations`

```json
{
  "subject": "Data Structures and Algorithms",
  "type": "EXAM",
  "targetDate": "2026-09-15T00:00:00Z",
  "description": "University entrance exam - Computer Science section"
}
```

**Expected:** `201 Created` with `status: "SETUP"`.

#### Test 5.2 — List Preparations

**Endpoint:** `GET /api/v1/learning/preparations`

**Expected:** Array with the preparation, sorted by target date.

#### Test 5.3 — Extract Topics (AI)

**Endpoint:** `POST /api/v1/learning/preparations/{prep_id}/extract-topics`

**Expected:** `200 OK` — Array of extracted topics:
```json
[
  { "id": "...", "title": "Arrays and Linked Lists", "estimatedMinutes": 45, "masteryScore": 0.0, "status": "NOT_STARTED" },
  { "id": "...", "title": "Trees and Graphs", "estimatedMinutes": 60, ... },
  ...
]
```

#### Test 5.4 — List Topics

**Endpoint:** `GET /api/v1/learning/preparations/{prep_id}/topics`

**Expected:** Same topics as above, ordered by `orderIndex`.

#### Test 5.5 — Start a Quiz (FULL_PRACTICE)

**Endpoint:** `POST /api/v1/learning/preparations/{prep_id}/quizzes`

```json
{
  "mode": "FULL_PRACTICE",
  "questionCount": 5
}
```

**Expected:** `201 Created` — Quiz session with generated questions.

#### Test 5.6 — Submit Quiz Answer

**Endpoint:** `POST /api/v1/learning/quizzes/{quiz_id}/answer`

```json
{
  "questionId": "{question_id_from_quiz}",
  "userAnswer": "B",
  "timeTakenSeconds": 15
}
```

**Expected:**
```json
{
  "questionId": "...",
  "isCorrect": true/false,
  "correctAnswer": "...",
  "explanation": "..."
}
```

#### Test 5.7 — Complete Quiz

**Endpoint:** `POST /api/v1/learning/quizzes/{quiz_id}/complete`

**Expected:**
```json
{
  "quizId": "...",
  "totalQuestions": 5,
  "correctCount": 3,
  "scorePercentage": 60.0,
  "topicBreakdown": [...],
  "weakAreas": ["Trees and Graphs"],
  "suggestedNextStep": "Focus on reviewing: Trees and Graphs..."
}
```

#### Test 5.8 — Mark Preparation Completed

**Endpoint:** `POST /api/v1/learning/preparations/{prep_id}/complete`

**Expected:** `200 OK` with `status: "COMPLETED"`.

---

### Phase 6: Study Plans

#### Test 6.1 — Generate Study Plan

**Endpoint:** `POST /api/v1/learning/study-plans`

```json
{
  "title": "Exam Prep: DSA",
  "goalDescription": "Master all data structures and algorithms topics",
  "deadline": "2026-09-01T00:00:00Z"
}
```

**Expected:** `201 Created` — Plan with distributed items across days.

#### Test 6.2 — List Study Plans

**Endpoint:** `GET /api/v1/learning/study-plans`

**Expected:** Array with active plans, each having `completionPercentage`, `daysRemaining`.

#### Test 6.3 — Complete a Plan Item

**Endpoint:** `POST /api/v1/learning/study-plans/{plan_id}/items/{item_id}/complete`

Use an item ID from the plan detail.

**Expected:** `200 OK` — `completedItems` incremented.

---

### Phase 7: Saved Resources

#### Test 7.1 — Save a Resource

**Endpoint:** `POST /api/v1/learning/resources`

```json
{
  "title": "Introduction to Algorithms (CLRS)",
  "url": "https://example.com/clrs-textbook",
  "sourceType": "external",
  "tags": ["textbook", "algorithms"]
}
```

**Expected:** `201 Created`

#### Test 7.2 — List Resources

**Endpoint:** `GET /api/v1/learning/resources`

**Expected:** Array with saved resource.

#### Test 7.3 — Update Tags

**Endpoint:** `PATCH /api/v1/learning/resources/{resource_id}/tags`

```json
{
  "tags": ["textbook", "algorithms", "reference"]
}
```

**Expected:** `200 OK` with updated tags.

#### Test 7.4 — Delete Resource

**Endpoint:** `DELETE /api/v1/learning/resources/{resource_id}`

**Expected:** `204 No Content`

---

### Phase 8: Documents

#### Test 8.1 — Generate Document (AI)

**Endpoint:** `POST /api/v1/learning/documents`

```json
{
  "type": "essay",
  "title": "Introduction to Binary Search Trees",
  "prompt": "Write a 500-word academic essay explaining binary search trees, their properties, and common operations.",
  "format": "pdf"
}
```

**Expected:** `201 Created` with document metadata and download URL.

#### Test 8.2 — List Documents

**Endpoint:** `GET /api/v1/learning/documents`

**Expected:** Paginated list with the generated document.

---

### Phase 9: Notifications

#### Test 9.1 — Get Notifications

**Endpoint:** `GET /api/v1/learning/notifications`

**Expected:** `200 OK` — Array of unread notifications (may be empty if no triggers have fired yet).

#### Test 9.2 — Mark as Read

**Endpoint:** `POST /api/v1/learning/notifications/{notification_id}/read`

(Only works if you have a notification from previous actions)

**Expected:** `204 No Content`

---

### Phase 10: Discovery

#### Test 10.1 — Get Recommendations

**Endpoint:** `GET /api/v1/learning/discovery`

**Expected:** `200 OK` — Array of recommendations (may be empty until background task runs).

---

### Phase 11: Behaviour & Reflection

#### Test 11.1 — Get Behaviour Profile

**Endpoint:** `GET /api/v1/learning/behaviour/profile`

**Expected:**
```json
{
  "preferredTimes": null,
  "avgSessionMinutes": null,
  "consistencyScore": null,
  "bestDayOfWeek": null,
  "dropoutRiskFactors": null
}
```

(Will populate after background tasks run and sessions accumulate)

#### Test 11.2 — Generate Reflection (AI)

**Endpoint:** `POST /api/v1/learning/reflections/generate`

```json
{
  "type": "weekly"
}
```

**Expected:** `201 Created`
```json
{
  "id": "...",
  "type": "weekly",
  "summary": "...",
  "activitiesLayer": {...},
  "progressLayer": {...},
  "achievementsLayer": {...},
  "recommendations": [...]
}
```

#### Test 11.3 — List Reflections

**Endpoint:** `GET /api/v1/learning/reflections`

**Expected:** Array with the generated reflection.

---

### Phase 12: Chat

#### Test 12.1 — Send Message

**Endpoint:** `POST /api/v1/learning/chat`

```json
{
  "message": "What should I study today?"
}
```

**Expected:** `200 OK`
```json
{
  "message": "Based on your goals and current progress...",
  "suggestedAction": { "type": "review_flashcards", "title": "..." }
}
```

#### Test 12.2 — Empty Message Validation

**Endpoint:** `POST /api/v1/learning/chat`

```json
{
  "message": ""
}
```

**Expected:** `422 Unprocessable Entity`

---

### Phase 13: Activity Feed

#### Test 13.1 — Get Activity Feed

**Endpoint:** `GET /api/v1/learning/activity-feed`

**Expected:** `200 OK`
```json
{
  "items": [...],
  "total": 0,
  "page": 1,
  "pageSize": 20
}
```

---

### Phase 14: Home (After Activity)

#### Test 14.1 — Get Home (Post-Activity)

**Endpoint:** `GET /api/v1/learning/home`

Now that you've created notes, flashcards, and preparations, verify:
- `progressSummary` reflects activity
- `dueReviews` may show flashcards (after 1 day)
- `recommendations` may be more personalized
- `todaysFocus` suggests relevant next step

---

### Phase 15: Commercial — Feature Tier & Capabilities

#### Test 15.1 — Get Capabilities (FREE User)

**Endpoint:** `GET /api/v1/learning/capabilities`

**Expected:** `200 OK`
```json
{
  "effectiveTier": "free",
  "isTrial": false,
  "trialDaysRemaining": null,
  "capabilities": [
    {
      "id": "flashcard_generation",
      "name": "Flashcard Generation",
      "freeDescription": "Generate up to 5 Q&A flashcards per note",
      "plusDescription": "Generate up to 10 flashcards per note with varied question types",
      "userLevel": "free",
      "lockedFeatures": ["limit:max_per_note=10", "..."],
      "upgradeValue": "Get 2x more flashcards per note with cloze, multiple-choice, and image-based cards"
    },
    ...
  ]
}
```

**Verify:**
- `effectiveTier` is `"free"` for unpaid users
- Each capability shows both free and plus descriptions
- `lockedFeatures` lists what FREE users can't access
- `upgradeValue` explains the benefit in learning terms

#### Test 15.2 — Quiz Mode Gate (PAST_PAPER_SIM blocked for FREE)

**Endpoint:** `POST /api/v1/learning/preparations/{prep_id}/quizzes`

```json
{
  "mode": "PAST_PAPER_SIM",
  "questionCount": 5
}
```

**Expected:** `403 Forbidden`
```json
{
  "detail": {
    "upgradeRequired": true,
    "reason": "'PAST_PAPER_SIM' mode requires Maigie Plus",
    "capability": "quiz_modes",
    "upgradeUrl": "/subscription",
    "trialAvailable": true,
    "upgradeValue": "Unlock adaptive quizzes that adjust difficulty..."
  }
}
```

#### Test 15.3 — Document Format Gate (DOCX blocked for FREE)

**Endpoint:** `POST /api/v1/learning/documents`

```json
{
  "type": "essay",
  "title": "Test",
  "prompt": "Write about anything",
  "format": "docx"
}
```

**Expected:** `403 Forbidden` with `upgradeRequired: true` and reason about format.

#### Test 15.4 — Document Style Gate (report blocked for FREE)

**Endpoint:** `POST /api/v1/learning/documents`

```json
{
  "type": "essay",
  "title": "Test",
  "prompt": "Write about anything",
  "format": "pdf",
  "style": "report"
}
```

**Expected:** `403 Forbidden` with reason about style.

#### Test 15.5 — Flashcard Generation (FREE quality — 5 cards max)

**Endpoint:** `POST /api/v1/learning/flashcards/generate/note/{note_id}`

**Expected:** `200 OK` — Array of at most 5 basic Q&A flashcards (no cloze, multi-choice).

#### Test 15.6 — Behaviour Profile (FREE — locked features shown)

**Endpoint:** `GET /api/v1/learning/behaviour/profile`

**Expected:** `200 OK`
```json
{
  "preferredTimes": {...},
  "avgSessionMinutes": ...,
  "consistencyScore": ...,
  "bestDayOfWeek": "...",
  "dropoutRiskFactors": null,
  "predictiveScheduling": null,
  "optimalStudyTimes": null,
  "tier": "free",
  "lockedFeatures": ["predictive_scheduling", "optimal_time_suggestions", "dropout_prevention"]
}
```

---

### Phase 16: Commercial — Trial System

#### Test 16.1 — Get Trial Status (Never Trialed)

**Endpoint:** `GET /api/v1/learning/trial/status`

**Expected:** `200 OK`
```json
{
  "isActive": false,
  "expired": false,
  "trialAvailable": true
}
```

#### Test 16.2 — Start Trial

**Endpoint:** `POST /api/v1/learning/trial/start`

**Expected:** `201 Created`
```json
{
  "isActive": true,
  "dayNumber": 1,
  "daysRemaining": 7,
  "startsAt": "2026-07-22T...",
  "endsAt": "2026-07-29T..."
}
```

#### Test 16.3 — Get Capabilities (During Trial)

**Endpoint:** `GET /api/v1/learning/capabilities`

**Expected:** `effectiveTier: "plus"`, `isTrial: true`, `trialDaysRemaining: 7`

#### Test 16.4 — Quiz Mode Gate (ADAPTIVE allowed during trial)

**Endpoint:** `POST /api/v1/learning/preparations/{prep_id}/quizzes`

```json
{
  "mode": "ADAPTIVE",
  "questionCount": 5
}
```

**Expected:** `201 Created` — Quiz starts successfully (trial = plus access).

#### Test 16.5 — Document Format (PPTX allowed during trial)

**Endpoint:** `POST /api/v1/learning/documents`

```json
{
  "type": "presentation",
  "title": "Trial Test Presentation",
  "prompt": "Create a short presentation about study techniques",
  "format": "pptx"
}
```

**Expected:** `201 Created` — Document generated in PPTX format.

#### Test 16.6 — Get Trial Status (Active)

**Endpoint:** `GET /api/v1/learning/trial/status`

**Expected:**
```json
{
  "isActive": true,
  "dayNumber": 1,
  "daysRemaining": 7,
  "showcaseSuggestions": [
    {"capabilityId": "quiz_modes", "title": "Try Adaptive Quizzes", ...},
    ...
  ]
}
```

#### Test 16.7 — Start Trial Again (should fail)

**Endpoint:** `POST /api/v1/learning/trial/start`

**Expected:** `400 Bad Request` — "You already have an active trial."

#### Test 16.8 — Trial Summary (should fail during active trial)

**Endpoint:** `POST /api/v1/learning/trial/summary`

**Expected:** `400 Bad Request` — "Trial summary available only after trial ends"

---

### Phase 17: Commercial — Conversion Triggers

#### Test 17.1 — Dismiss a Trigger

**Endpoint:** `POST /api/v1/learning/triggers/active_learner_discovery/dismiss`

**Expected:** `204 No Content`

**Verify:** The trigger won't show again for 30 days for this trigger type.

---

### Phase 18: Commercial — Value Summary

#### Test 18.1 — Get Value Summary (FREE user — should fail)

**Endpoint:** `GET /api/v1/learning/value-summary`

**Expected:** `403 Forbidden` — "Value summary is for Plus subscribers"

#### Test 18.2 — Get Value Summary (PLUS/Trial user)

**Endpoint:** `GET /api/v1/learning/value-summary`

(Run while trial is active)

**Expected:** `200 OK`
```json
{
  "periodStart": "...",
  "periodEnd": "...",
  "headline": "This month: ...",
  "detailMessage": "...",
  "metrics": {
    "aiAssistedSessions": 2,
    "documentsGenerated": 1,
    "timeSavedMinutes": 15,
    "flashcardsReviewed": 0,
    "studyPlanItemsCompleted": 0,
    "goalsAchieved": 0,
    "quizzesTaken": 1
  },
  "topFeaturesUsed": ["Document Generation", "Quiz Practice"],
  "plusExclusiveFeaturesUsed": ["document_generation", "quiz_modes"]
}
```

---

### Phase 19: Commercial — Milestones

#### Test 19.1 — Get Milestones (Empty Initially)

**Endpoint:** `GET /api/v1/learning/milestones`

**Expected:** `200 OK`
```json
{
  "milestones": []
}
```

#### Test 19.2 — Share Milestone (Invalid)

**Endpoint:** `POST /api/v1/learning/milestones/nonexistent/share`

**Expected:** `404 Not Found`

---

### Phase 20: Commercial — Educator Transition

#### Test 20.1 — Get Educator Readiness (New User)

**Endpoint:** `GET /api/v1/learning/educator-readiness`

**Expected:** `200 OK`
```json
{
  "isReady": false,
  "signalsMet": 0,
  "totalSignals": 4,
  "signals": {
    "maturity": false,
    "content_created": false,
    "quiz_performance": false,
    "plan_completion": false
  },
  "message": null
}
```

#### Test 20.2 — Start Circle Trial (Not Ready — should fail)

**Endpoint:** `POST /api/v1/learning/educator-transition/circle-trial`

**Expected:** `400 Bad Request` — "You need to meet 3 of 4 educator readiness signals."

---

## Error Handling Verification (Commercial)

### Test: 403 for Gated Feature Without Subscription

Any PLUS-only capability requested by a FREE user (no trial) returns:
```json
{
  "detail": {
    "upgradeRequired": true,
    "reason": "...",
    "capability": "...",
    "upgradeUrl": "/subscription",
    "trialAvailable": true/false,
    "upgradeValue": "..."
  }
}
```

### Test: Trial Cooldown Enforcement

After trial expires, attempting `POST /api/v1/learning/trial/start` within 180 days returns:
`400 Bad Request` — "Trial available again on [date]."

---

## Notes

- **AI-dependent endpoints** (summary, retake, topic extraction, quiz generation, flashcard generation, document generation, reflection, chat) require a valid `GEMINI_API_KEY` in `.env`. Without it, these will return 500 errors.
- **Background tasks** (daily plan, engagement check, behaviour analysis, recommendations, retention check, value summary generation, trial expiry) run via Celery beat — they won't fire during manual testing unless you run the worker: `poetry run celery -A src.core.celery_app:celery_app worker -Q default,heavy --loglevel=info`
- **Flashcard due dates**: Cards won't appear in "due" until after their `next_review_at` timestamp passes (1 day after creation for new cards).
- **Swagger "Try it out"**: Click the endpoint → "Try it out" → fill in parameters/body → "Execute"
- **Trial testing**: The 7-day trial can be tested by manipulating `trialEndsAt` in the database to a past date, then calling the trial expiry task manually.
- **Conversion triggers**: Triggers only fire for FREE users with 3+ days maturity and 72h since last trigger. To test, ensure your user has been active for several days and hasn't seen a trigger recently.
- **Feature tier gates**: To test PLUS features without a real subscription, start a trial first (`POST /trial/start`). All PLUS capabilities become accessible during the trial.

---

## Error Handling Verification

### Test: 404 for Non-Existent Resource

**Endpoint:** `GET /api/v1/learning/notes/nonexistent-id-12345`

**Expected:** `404`

### Test: 422 for Invalid Body

**Endpoint:** `POST /api/v1/learning/flashcards`

```json
{
  "front": "Question without an answer"
}
```

**Expected:** `422` (missing required field `back`)

### Test: 401 Without Token

Remove authorization (click 🔒 Authorize → Logout), then:

**Endpoint:** `GET /api/v1/learning/home`

**Expected:** `401 Unauthorized`
