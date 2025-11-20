# AI & Vector Search Design

## Ingestion

* On note/course/resource create or update → enqueue indexing job (background worker) to compute embeddings and store in vector DB.

## Semantic Search

* Use embeddings to query relevant notes/resources.
* Combine with lightweight retrieval-augmented generation (RAG) for chat: get top-k docs → build prompt → call LLM.

## Prompting

* Keep prompt templates in `libs/ai/` and version them.
* Always include a short context (user preferences, active course, recent activity) and a retrieval buffer.

## Rate Limits & Quotas

* Per-user and global limits; cache LLM responses for identical queries.

---

# AI Intent → Action Mapping Table

Below is the system‑level mapping of what the AI should do for each type of user intent. These mappings are used by the backend (FastAPI) AI service to interpret conversation and trigger structured actions.

## 1. Learning / Course Creation Intents

| User Intent (Natural)                      | AI Interpretation        | Backend Actions                                                                                                |
| ------------------------------------------ | ------------------------ | -------------------------------------------------------------------------------------------------------------- |
| "I want to learn X"                        | Create a course outline  | → LLM generates syllabus → Create `Course` + `Modules` → Index for search → Return summary to UI               |
| "Teach me about Y"                         | Course/topic exploration | → Retrieve relevant notes/resources → Summaries → Suggestions to create a course if structured learning needed |
| "I'm starting a new class on Z"            | Add external course      | → Create `Course` with minimal metadata → Ask user to upload syllabus or extract from text                     |
| "Explain what topics I need for this exam" | Derive learning pathway  | → AI generates outline → Offer auto‑create course + set schedule                                               |

## 2. Goal Setting Intents

| User Intent                                  | AI Interpretation                  | Backend Actions                                                         |
| -------------------------------------------- | ---------------------------------- | ----------------------------------------------------------------------- |
| "I want to pass my exam in 6 weeks"          | Create academic goal with deadline | → Create `Goal` → Estimate workload → Attach relevant courses           |
| "Help me improve in math this month"         | High-level skill improvement goal  | → Generate milestones → Create `Goal` with weekly checkpoints           |
| "I want to finish Chapter 5 today"           | Short-term goal                    | → Create micro-goal → Add to today's schedule                           |
| "What should my goals be for this semester?" | Goal planning                      | → Use existing courses → Generate 3–5 recommended goals → User approves |

## 3. Scheduling Intents

| User Intent                            | AI Interpretation          | Backend Actions                                                                     |
| -------------------------------------- | -------------------------- | ----------------------------------------------------------------------------------- |
| "Plan my week"                         | Weekly schedule generation | → Fetch courses, goals → Create recurring schedule blocks → Save to `ScheduleBlock` |
| "Help me study today"                  | Daily study plan           | → Review time availability → Generate optimized timetable → Add reminders           |
| "Give me a timetable for this month"   | Long-range schedule        | → Create 4-week plan → Group by focus areas → Save to schedule + forecast           |
| "Schedule 2 hours of reading tomorrow" | Specific time block        | → Create one-off schedule block                                                     |

## 4. Resource Discovery Intents

| User Intent                                    | AI Interpretation          | Backend Actions                                                   |
| ---------------------------------------------- | -------------------------- | ----------------------------------------------------------------- |
| "Give me resources on X"                       | Resource recommendation    | → Query semantic search → LLM ranking → Create `Resource` entries |
| "What should I watch/read to understand this?" | Guided learning            | → Course/topic detection → Recommend videos/articles              |
| "Find me practice questions"                   | Assessment resource search | → Retrieve curated practice sets → Recommend based on difficulty  |
| "Show me more like this"                       | Similar resource query     | → Vector similarity search → Add similar links                    |

## 5. Note-taking & Summaries Intents

| User Intent                      | AI Interpretation      | Backend Actions                                   |
| -------------------------------- | ---------------------- | ------------------------------------------------- |
| "Summarize this"                 | Content summarization  | → Summaries using LLM → Store summary as `Note`   |
| "Turn this into study notes"     | Note structuring       | → Convert raw text → bullet notes → Create `Note` |
| "Extract key points"             | Information extraction | → Create structured note → Index embedding        |
| "Connect this note to my course" | Link note to course    | → Update `Note.linkedCourseId`                    |

## 6. Progress Tracking Intents

| User Intent          | AI Interpretation      | Backend Actions                                              |
| -------------------- | ---------------------- | ------------------------------------------------------------ |
| "How am I doing?"    | Progress check         | → Analyze goal progress, schedule adherence → Return metrics |
| "Am I on track?"     | Forecast check         | → Run forecast model → Show probability of achieving goals   |
| "Update my progress" | Manual progress update | → Create progress event → Recalculate goal progress          |

## 7. Assistant Control Intents

| User Intent                  | AI Interpretation          | Backend Actions                                            |
| ---------------------------- | -------------------------- | ---------------------------------------------------------- |
| "Forget that"                | Clear conversation context | → Reset conversation memory                                |
| "Save this"                  | Save important content     | → Create `Note` or `Resource`                              |
| "Remind me later"            | Set reminder               | → Create `Reminder` entry → Queue notification             |
| "Focus on this course today" | Set active context         | → Prioritize specific course in AI context/recommendations |

## 8. Voice Commands (mapped same as text)

Examples:

* "Start a study session" → Begin `Session` timer
* "What should I do next?" → AI computes next immediate task
* "Explain topic X" → AI generates micro-lesson
* "Plan my tomorrow" → Daily schedule generation

---

# Full LLM Prompt Architecture (System Prompts + Formatting Rules)

## Purpose

Provide deterministic, structured, and safe outputs from the LLM so the backend can parse actions reliably (create course, create schedule, recommend resources, etc.). Use a two-layer approach:

1. **System prompt**: global rules (tone, constraints, safety).
2. **Task prompt**: dynamic context + templates for each intent type.

## System Prompt (example)

```
You are Maigie's AI Assistant. Be concise, helpful, and educational. Always try to ask clarifying questions when user intent is ambiguous. Do NOT make up dates or claim access to a user's private files. When asked to perform an action (create course, set goal, schedule), return a JSON payload according to the requested `output_schema` and include a short human-readable `summary` field. If unsure, ask one clarifying question. Keep responses under 500 words unless summarizing large content.
```

## Output Formatting Rules

* All action-oriented responses must include a top-level JSON block with exact field `action` and `payload`. Use `JSON_ONLY` wrapper.
* After the JSON block the assistant may include an optional natural-language `explanation` for the user.
* If multiple actions are proposed, include an array `actions`.
* Validate that generated dates are ISO 8601 (`YYYY-MM-DD` or full timestamp).

### Example: JSON_ONLY wrapper

```
JSON_ONLY
{ "action": "create_course", "payload": { ... } }
END_JSON

Human explanation: ...
```

## Example Task Prompt (Create Course)

```
System: <system prompt above>
User: "I want to learn Data Structures in 6 weeks"
Task: create_course
Context: { user_id: U123, timezone: 'Africa/Lagos', active_courses: [...] }
Output Schema: {
  "action": "create_course",
  "payload": {
    "title": "string",
    "description": "string",
    "modules": [{ "title": "string", "duration_days": int, "topics": ["string"] }],
    "start_date": "YYYY-MM-DD",
    "end_date": "YYYY-MM-DD",
    "estimated_hours": float
  }
}

Instructions: produce a JSON_ONLY block strictly following the Output Schema. If any dates are unknown, ask a clarifying question instead of guessing.
```

## Failure Modes & Mitigations

* If LLM returns invalid JSON: run it through a JSON repair pass (small parser + heuristics) then verify schema; if still invalid, ask user a clarifying question.
* For date conflicts: compare generated dates to user-provided deadlines and clamp by availability; present choices to user.
* For hallucinated resources: include `source` metadata and prefer high-precision retrieval (embedding retrieval) rather than pure generation.

---

# Decision Engine Flowchart (AI Intent Routing)

This is the middleware in the backend which converts LLM outputs / user messages into concrete system actions.

## Overview Steps

1. **Receive message** (REST or WebSocket) with auth context and current `active_context` (course/goal selection).
2. **Preprocessing**: sanitize input, detect language, run intent classifier (lightweight model or rules).
3. **Context enrichment**: fetch relevant user data (courses, recent notes, schedule availability) and prepare retrieval buffer.
4. **LLM call**: send system + task prompt to LLM with `output_schema` indicating expected action(s).
5. **Postprocessing**:

   * Validate LLM JSON against schema.
   * If `action` present → route to `Action Dispatcher`.
   * If `clarify` proposed → send clarifying question to user.
6. **Action Dispatcher**: maps `action` to domain service (CourseService, ScheduleService, ResourceService).
7. **Transaction & Event Emission**: perform DB change inside a transactional boundary, then emit domain events.
8. **Response to client**: send `result` back via REST response or WebSocket push.

## Edge Cases

* Ambiguous user intent: capture and ask clarifying question; do not auto-create entities.
* Permission checks: ensure user is allowed to create/modify the target entity.
* Quotas & costs: if action would trigger expensive LLM calls or many creations, throttle and confirm.

---

# Backend Event Architecture (Signals When AI Creates Entities)

Events allow other systems (workers, notif service, analytics) to react to AI-created entities. Use an event bus (Redis streams, Kafka, or simple Postgres pub/sub).

## Event Schema (JSON Common Envelope)

```
{
  "event_id": "uuid",
  "type": "string",            // e.g. course.created, schedule.created
  "timestamp": "ISO8601",
  "user_id": "uuid",
  "payload": { ... },           // domain payload
  "metadata": { "source": "ai" | "user", "request_id": "uuid" }
}
```

## Core Events

* `course.created` — payload: course object
* `course.updated`
* `goal.created`
* `goal.updated`
* `schedule.created`
* `schedule.updated`
* `resource.recommended` — payload: resource objects + scoring
* `ai.conversation.logged` — payload: conversation id + snapshot

## Example Flow

* AI action produces `create_course` JSON → Backend validates → Creates `Course` in DB inside transaction → Emit `course.created` to event bus → Worker `indexing-service` listens and queues embedding job → Another worker `notification-service` may send onboarding tips.

## Event Consumers

* Indexing worker (embeddings)
* Notification service (push/email)
* Analytics (track conversion & engagement)
* Sync service (mobile offline sync)

