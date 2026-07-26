# Personal Learning — Comprehensive User Flow

> **"What does this learner need next?"**
>
> The Personal Learning section is the emotional center of Maigie. It's a Home, not a dashboard.
> The learner doesn't plan, organize, or decide. They simply open Maigie, and everything is ready.

---

## Table of Contents

1. [High-Level Journey Map](#1-high-level-journey-map)
2. [Flow 1: First-Time Onboarding](#2-flow-1-first-time-onboarding)
3. [Flow 2: Daily Return — The Home Experience](#3-flow-2-daily-return--the-home-experience)
4. [Flow 3: Flashcard Creation & Spaced Repetition](#4-flow-3-flashcard-creation--spaced-repetition)
5. [Flow 4: Notes Management](#5-flow-4-notes-management)
6. [Flow 5: Exam/Certification Preparation](#6-flow-5-examcertification-preparation)
7. [Flow 6: Quiz Practice](#7-flow-6-quiz-practice)
8. [Flow 7: Study Plan Generation & Execution](#8-flow-7-study-plan-generation--execution)
9. [Flow 8: Document Generation](#9-flow-8-document-generation)
10. [Flow 9: Contextual Intelligence Chat](#10-flow-9-contextual-intelligence-chat)
11. [Flow 10: Saved Resources (Personal Library)](#11-flow-10-saved-resources-personal-library)
12. [Flow 11: Behaviour & Reflection](#12-flow-11-behaviour--reflection)
13. [Flow 12: Notifications & Re-engagement](#13-flow-12-notifications--re-engagement)
14. [Flow 13: Discovery & Recommendations](#14-flow-13-discovery--recommendations)
15. [Flow 14: Cross-Domain Flow (Personal ↔ Collaborative)](#15-flow-14-cross-domain-flow-personal--collaborative)
16. [Flow 15: Commercial — Trial & Capabilities](#16-flow-15-commercial--trial--capabilities)
17. [Stage Progression Model](#17-stage-progression-model)
18. [API Endpoint Map](#18-api-endpoint-map)

---

## 1. High-Level Journey Map

```
┌─────────────────────────────────────────────────────────────────────────────────────┐
│                          LEARNER LIFECYCLE                                           │
│                                                                                     │
│  ┌──────────┐    ┌──────────────┐    ┌──────────────┐    ┌──────────────────────┐   │
│  │  FRESH   │───▶│ PURPOSE_SET  │───▶│ SETTING_UP   │───▶│       ACTIVE         │   │
│  │ (Day 0)  │    │  (Day 0-1)   │    │  (Day 0-1)   │    │   (Day 1 onward)     │   │
│  └──────────┘    └──────────────┘    └──────────────┘    └──────────────────────┘   │
│       │                 │                    │                       │               │
│  No profile       Purpose chosen       Auto-setup runs        Full features         │
│  No content       Awaiting subjects    Creating content       Guidance engine        │
│  Show welcome     Show subject form    Show "preparing"       Spaced repetition     │
│                                                               Study plans           │
│                                                               Reflections           │
└─────────────────────────────────────────────────────────────────────────────────────┘
```

### Stages at a Glance

| Stage | Condition | Home Behavior |
|-------|-----------|---------------|
| `fresh` | No LearningProfile exists | Welcome message, purpose selector as primary CTA |
| `purpose_set` | Purpose set, no subjects yet | Encourage subject/goal selection |
| `setting_up` | Subjects set, auto-setup running | "Preparing your learning environment..." |
| `active` | Content exists, learner is engaged | Full guidance engine, due reviews, study plans |

---

## 2. Flow 1: First-Time Onboarding

### Trigger
User signs up or logs in for the first time. No `LearningProfile` exists.

### Flow Diagram

```
┌────────────────────┐
│   User Opens App   │
└─────────┬──────────┘
          │
          ▼
┌────────────────────┐     GET /api/v1/learning/home
│  Home (stage:fresh)│────────────────────────────────┐
│                    │                                │
│  "What brings you  │                                │
│   to Maigie today?"│                                │
└─────────┬──────────┘                                │
          │ User selects purpose                      │
          ▼                                           │
┌────────────────────┐     POST /api/v1/learning/onboarding/purpose
│  Purpose Selected  │     body: { purpose: "exam_prep" }
│                    │
│  Options:          │
│  • exam_prep       │
│  • skill_building  │
│  • course_completion│
│  • professional_   │
│    certification   │
│  • general_learning│
└─────────┬──────────┘
          │
          ▼
┌────────────────────┐     POST /api/v1/learning/onboarding/subjects
│  Subjects & Goals  │     body: { subjects: ["Python", "ML"], goals: "Pass AWS cert" }
│                    │
│  • Tag-style input │
│    for subjects    │
│  • Free-text goal  │
│    field           │
└─────────┬──────────┘
          │
          ▼
┌────────────────────┐
│   Auto-Setup Runs  │  (System creates preparation, extracts topics,
│   (stage:          │   generates flashcards, builds study plan)
│    setting_up)     │
└─────────┬──────────┘
          │
          ▼
┌────────────────────┐     POST /api/v1/learning/onboarding/complete
│  Onboarding Done   │     (marks user.isOnboarded = true)
│  → stage: active   │
└────────────────────┘
```

### Auto-Setup Pipeline (Backend)

When subjects are set, the system automatically:

1. **Creates a Preparation** — type derived from purpose (exam_prep → EXAM, skill_building → PROJECT, etc.)
2. **Extracts Topics via LLM** — AI identifies key study topics from subjects and goals
3. **Generates Initial Flashcards** — AI creates flashcards for core concepts
4. **Creates Study Plan** — distributes topics across 30 days (default deadline)

The learner sees a "Setting up your learning environment..." state, then returns to a fully prepared Home.

### Onboarding Exit Conditions

Onboarding ends when EITHER:
- **Time-based**: `maturity_days > 7` (fallback for inactive users)
- **Activity-based**: Purpose is set AND at least one content item created (note, flashcard, or preparation)

### Frontend Components

| Step | Component | Backend Endpoint |
|------|-----------|-----------------|
| Purpose selection | `PurposeSelector` | `POST /onboarding/purpose` |
| Subject/goal input | `SubjectGoalForm` | `POST /onboarding/subjects` |
| Loading state | Setup spinner | (auto-setup runs inline) |
| Completion | Redirect to Home | `POST /onboarding/complete` |

---

## 3. Flow 2: Daily Return — The Home Experience

### Trigger
Authenticated learner opens Maigie (GET `/api/v1/learning/home`).

### What the Home Returns

The Home service aggregates data from multiple sources concurrently:

```
┌─────────────────────────────────────────────────────────────────────┐
│                         HOME RESPONSE                               │
├─────────────────────────────────────────────────────────────────────┤
│                                                                     │
│  ┌─────────────────────────────────────────────────────────┐        │
│  │ GREETING (Hero Card)                                    │        │
│  │ "You have 5 flashcards ready for review. Quick recall   │        │
│  │  keeps knowledge fresh."                                │        │
│  └─────────────────────────────────────────────────────────┘        │
│                                                                     │
│  ┌─────────────────────────────────────────────────────────┐        │
│  │ TODAY'S FOCUS                                           │        │
│  │ title: "Review 5 flashcards"                            │        │
│  │ reason: "Spaced repetition — reviewing now prevents     │        │
│  │          forgetting."                                   │        │
│  │ estimatedMinutes: 3                                     │        │
│  │ type: "review_flashcards"                               │        │
│  │ actionData: { action: "start_review" }                  │        │
│  └─────────────────────────────────────────────────────────┘        │
│                                                                     │
│  ┌─────────────────────────────────────────────────────────┐        │
│  │ PROGRESS SUMMARY                                        │        │
│  │ • currentStreak: 14 days                                │        │
│  │ • activeDaysThisWeek: ["Mon", "Tue", "Wed", "Thu"]      │        │
│  │ • cardsReviewedThisWeek: 42                             │        │
│  │ • cardsMastered: 87                                     │        │
│  │ • consistencyScore: 85%                                 │        │
│  └─────────────────────────────────────────────────────────┘        │
│                                                                     │
│  ┌─────────────────────────────────────────────────────────┐        │
│  │ DUE REVIEWS (sorted by urgency)                         │        │
│  │ • Flashcard: "What is backpropagation?" — due 2h ago    │        │
│  │ • Flashcard: "Explain gradient descent" — due now       │        │
│  │ • Flashcard: "Define loss function" — due in 30min      │        │
│  └─────────────────────────────────────────────────────────┘        │
│                                                                     │
│  ┌─────────────────────────────────────────────────────────┐        │
│  │ READY FOR YOU (system-prepared content)                  │        │
│  │ • Study plan item: "Chapter 4 — Neural Networks"        │        │
│  │ • New flashcards generated from your latest note        │        │
│  └─────────────────────────────────────────────────────────┘        │
│                                                                     │
│  ┌─────────────────────────────────────────────────────────┐        │
│  │ SCHEDULE BLOCKS (today)                                  │        │
│  │ • 09:00-09:30 — Review flashcards                       │        │
│  │ • 14:00-15:00 — Study: Neural Networks                  │        │
│  └─────────────────────────────────────────────────────────┘        │
│                                                                     │
│  ┌─────────────────────────────────────────────────────────┐        │
│  │ COMMERCIAL (if applicable)                               │        │
│  │ • premiumSuggestion | trialStatus | milestone           │        │
│  └─────────────────────────────────────────────────────────┘        │
│                                                                     │
└─────────────────────────────────────────────────────────────────────┘
```

### Today's Focus Priority System

The guidance engine selects focus based on strict priority:

1. **Due flashcards** (always highest — spaced repetition is time-sensitive)
2. **Today's study plan items** (scheduled commitments)
3. **LLM-computed guidance** (intelligent fallback based on full learner state)

### Re-engagement (Away > 7 days)

If the learner has been absent for 7+ days, the Home includes:
```json
{
  "reEngagement": {
    "message": "Welcome back. No pressure — even one flashcard keeps momentum alive.",
    "suggestedAction": { "type": "review_flashcards", "title": "Review one flashcard" }
  }
}
```

Never guilt. Always low-effort starting actions.

### Progressive Enrichment (Maturity)

| Maturity | Home Behavior |
|----------|---------------|
| Days 1-7 | Discovery, first-step suggestions, no analytics |
| Days 7-30 | Pattern-based suggestions, consistency score |
| Days 30-100 | Deeper insights, comparative reflections |
| Days 100+ | Historical context, long-term trends |

---

## 4. Flow 3: Flashcard Creation & Spaced Repetition

### Overview
Flashcards are the heartbeat of retention. They can be created manually, generated from notes, or generated from topics. The SM-2 algorithm handles scheduling.

### Creation Paths

```
                    ┌──────────────────────┐
                    │  CREATE FLASHCARD    │
                    └──────────┬───────────┘
                               │
          ┌────────────────────┼────────────────────┐
          │                    │                    │
          ▼                    ▼                    ▼
┌──────────────────┐ ┌──────────────────┐ ┌──────────────────┐
│  Manual Create   │ │  From Note (AI)  │ │  From Topic (AI) │
│                  │ │                  │ │                  │
│ POST /flashcards │ │ POST /flashcards/│ │ POST /flashcards/│
│ {front, back,    │ │ generate/note/   │ │ generate/topic/  │
│  deckId?}        │ │ {note_id}        │ │ {topic_id}       │
└──────────────────┘ └──────────────────┘ └──────────────────┘
```

### Review Flow (Spaced Repetition)

```
┌──────────────────┐
│  Home shows      │  "You have 5 flashcards due"
│  due reviews     │
└────────┬─────────┘
         │ User taps "Start Review"
         ▼
┌──────────────────┐     GET /api/v1/learning/flashcards/due
│  Fetch Due Cards │     Returns cards sorted by urgency (most overdue first)
└────────┬─────────┘
         │
         ▼
┌──────────────────┐
│  REVIEW SESSION  │
│                  │
│  1. Show FRONT   │
│  2. User thinks  │
│  3. Reveal BACK  │
│  4. User rates   │
│     quality 0-5  │
│                  │
│  Quality scale:  │
│  0 = Blackout    │
│  1 = Wrong       │
│  2 = Hard        │
│  3 = Hesitant    │
│  4 = Good        │
│  5 = Easy        │
└────────┬─────────┘
         │ POST /api/v1/learning/flashcards/{card_id}/review
         │ body: { quality: 4 }
         ▼
┌──────────────────┐
│  SM-2 UPDATE     │
│                  │
│  • Recalculate   │
│    ease_factor   │
│  • Set new       │
│    interval      │
│  • Compute       │
│    next_review_at│
│                  │
│  If quality < 3: │
│    → Reset to    │
│      1 day       │
│    → Increment   │
│      lapse_count │
└────────┬─────────┘
         │
         ▼
┌──────────────────┐
│  Next card or    │
│  Session done    │
└──────────────────┘
```

### SM-2 Parameters (per card)

| Field | Initial | Description |
|-------|---------|-------------|
| `intervalDays` | 1 | Days until next review |
| `repetitionCount` | 0 | Successful reviews in sequence |
| `easeFactor` | 2.5 | Difficulty multiplier (min 1.3) |
| `nextReviewAt` | now | When card is next due |
| `lapseCount` | 0 | Times card was "forgotten" (quality < 3) |

### Deck Organization

Cards can be organized into decks linked to courses, topics, or preparations:
- `POST /api/v1/learning/decks` — Create deck
- `GET /api/v1/learning/decks` — List decks
- `GET /api/v1/learning/decks/{deck_id}/flashcards` — Cards in deck

### Statistics

`GET /api/v1/learning/flashcards/stats` returns:
- `total` — Total cards created
- `dueToday` — Cards due for review now
- `masteredCount` — Cards with interval > 21 days
- `averageEaseFactor` — Overall difficulty trend

---

## 5. Flow 4: Notes Management

### Overview
Notes are the learner's personal study artifacts. They support full CRUD, AI-powered summaries, AI retakes (rewriting), flashcard generation, and importing to collaborative spaces.

### Core CRUD Flow

```
┌─────────────────────────────────────────────────────────────────┐
│                     NOTES LIFECYCLE                              │
│                                                                 │
│  CREATE ──────▶ READ/LIST ──────▶ UPDATE ──────▶ DELETE          │
│  POST /notes    GET /notes       PATCH /notes/  DELETE /notes/   │
│                 GET /notes/{id}  {id}           {id}            │
│                                                                 │
│  Supports:                                                      │
│  • Pagination (page, size)                                      │
│  • Search by title/content                                      │
│  • Filter by tag, course, topic, archived                       │
└─────────────────────────────────────────────────────────────────┘
```

### AI Operations on Notes

```
┌──────────────────┐
│    NOTE EXISTS   │
└────────┬─────────┘
         │
    ┌────┴─────────────────────────────────┐
    │              │                        │
    ▼              ▼                        ▼
┌────────┐   ┌────────────┐   ┌────────────────────────┐
│SUMMARY │   │  RETAKE    │   │ GENERATE FLASHCARDS    │
│        │   │ (AI rewrite│   │                        │
│POST    │   │  with      │   │ POST /flashcards/      │
│/notes/ │   │  better    │   │ generate/note/{id}     │
│{id}/   │   │  structure)│   │                        │
│summary │   │            │   │ AI extracts key        │
│        │   │POST /notes/│   │ concepts → creates     │
│Generates│  │{id}/retake │   │ flashcards for each    │
│concise  │  └────────────┘   └────────────────────────┘
│summary  │
└─────────┘
```

### Attachments

```
POST /notes/{note_id}/attachments     — Add attachment (filename, url, size)
DELETE /notes/{note_id}/attachments/{attachment_id}  — Remove attachment
```

### Import to Space

```
POST /notes/{note_id}/import
body: { spaceId: "space_abc" }

Creates a copy in the target Learning Space (only if learner is a member).
Original note is preserved in personal context.
```

### Note Data Model

| Field | Description |
|-------|-------------|
| `title` | Required, 1-500 chars |
| `content` | Rich text body |
| `summary` | AI-generated or manual |
| `courseId` | Link to a course |
| `topicId` | Link to a topic |
| `tags` | Array of string tags |
| `archived` | Soft-delete toggle |
| `voiceRecordingUrl` | Audio note support |
| `attachments` | Files linked to note |

---

## 6. Flow 5: Exam/Certification Preparation

### Overview
Preparations are structured goal-oriented study containers. They support multiple types (EXAM, CERTIFICATION, INTERVIEW, PRESENTATION, ASSIGNMENT, PROJECT) and include materials, topic extraction, quizzes, and study plan generation.

### Full Preparation Lifecycle

```
┌───────────────┐     POST /api/v1/learning/preparations
│   CREATE      │     body: { subject, type, examDate, description }
│   PREPARATION │
└───────┬───────┘
        │ Status: SETUP
        ▼
┌───────────────┐     POST /preparations/{id}/materials
│   UPLOAD      │     (PDFs, notes, syllabi)
│   MATERIALS   │
└───────┬───────┘
        │
        ▼
┌───────────────┐     POST /preparations/{id}/extract-topics
│   EXTRACT     │     AI analyzes materials → identifies key topics
│   TOPICS      │     Returns: [{title, description, estimatedStudyTime}]
└───────┬───────┘
        │
        ▼
┌───────────────┐     POST /preparations/{id}/study-plan
│   GENERATE    │     AI distributes topics across days until deadline
│   STUDY PLAN  │     Respects learner's behaviour patterns
└───────┬───────┘
        │
        ├───────────────────────────────────┐
        │                                   │
        ▼                                   ▼
┌───────────────┐                 ┌───────────────┐
│  DAILY STUDY  │                 │  TAKE QUIZ    │
│  (complete    │                 │  (test        │
│   plan items) │                 │   knowledge)  │
└───────┬───────┘                 └───────┬───────┘
        │                                   │
        └───────────────┬───────────────────┘
                        │
                        ▼
              ┌───────────────┐     POST /preparations/{id}/complete
              │   MARK        │     (or auto-complete when target date passes)
              │   COMPLETED   │
              └───────────────┘
```

### Preparation Types

| Type | Purpose | Derived From |
|------|---------|-------------|
| `EXAM` | Academic exam prep | `exam_prep` purpose |
| `CERTIFICATION` | Professional cert | `professional_certification` purpose |
| `INTERVIEW` | Job interview prep | Manual creation |
| `PRESENTATION` | Presentation prep | Manual creation |
| `ASSIGNMENT` | Homework/assignment | `course_completion` purpose |
| `PROJECT` | Project work | `skill_building` / `general_learning` |

### Material Management

Materials uploaded to a preparation serve as source content for:
- Topic extraction (LLM reads materials to identify study topics)
- Quiz question generation (questions derived from materials)
- Flashcard generation (key concepts extracted)

Each material has: `filename`, `url`, `extractedText`, `fileType`, `size`, `category`, `label`

### API Endpoints

| Action | Method | Path |
|--------|--------|------|
| Create preparation | POST | `/preparations` |
| List preparations | GET | `/preparations` |
| Get preparation | GET | `/preparations/{id}` |
| Update preparation | PATCH | `/preparations/{id}` |
| Delete preparation | DELETE | `/preparations/{id}` |
| Upload material | POST | `/preparations/{id}/materials` |
| Extract topics | POST | `/preparations/{id}/extract-topics` |
| List topics | GET | `/preparations/{id}/topics` |
| Generate study plan | POST | `/preparations/{id}/study-plan` |
| Mark completed | POST | `/preparations/{id}/complete` |

---

## 7. Flow 6: Quiz Practice

### Overview
Quizzes test the learner's knowledge against their preparation topics. AI generates questions, the learner answers them, and the system tracks per-topic performance to identify weak areas.

### Quiz Flow

```
┌────────────────────┐
│ SELECT QUIZ MODE   │
│                    │
│ • FULL_PRACTICE    │  All topics, comprehensive
│ • WEAK_AREAS       │  Topics where score < 70%
│ • TOPIC_FOCUS      │  Single topic deep-dive
│ • PAST_PAPER_SIM   │  Simulated exam conditions
│ • QUICK_REVIEW     │  Fast, focused review
└─────────┬──────────┘
          │ POST /preparations/{prep_id}/quizzes
          │ body: { mode, topicId?, questionCount? }
          ▼
┌────────────────────┐
│ QUIZ SESSION       │  AI generates questions from preparation topics
│ STARTS             │  Returns: quiz_id, questions[]
└─────────┬──────────┘
          │
          ▼ (for each question)
┌────────────────────┐
│ QUESTION DISPLAYED │
│                    │
│ User writes answer │
│                    │
│ POST /quizzes/     │
│ {quiz_id}/answer   │
│ { questionId,      │
│   userAnswer,      │
│   timeTakenSeconds }│
└─────────┬──────────┘
          │ Returns: { correct, explanation, correctAnswer }
          │
          ▼ (after all questions)
┌────────────────────┐
│ COMPLETE QUIZ      │  POST /quizzes/{quiz_id}/complete
│                    │  body: { durationSeconds? }
└─────────┬──────────┘
          │
          ▼
┌────────────────────┐
│ QUIZ SUMMARY       │
│                    │
│ • Overall score    │
│ • Per-topic        │
│   breakdown        │
│ • Weak areas       │
│   identified       │
│ • Recommendations: │
│   "Review Neural   │
│    Networks topic" │
└────────────────────┘
```

### Quiz Modes Explained

| Mode | What it does | When to use |
|------|-------------|-------------|
| `FULL_PRACTICE` | Questions covering all topics | General review, confidence check |
| `WEAK_AREAS` | Only topics with score < 70% | Targeted improvement |
| `TOPIC_FOCUS` | Single topic, deeper questions | Mastering specific concepts |
| `PAST_PAPER_SIM` | Timed, exam-like conditions | Pre-exam simulation |
| `QUICK_REVIEW` | Fewer questions, faster pace | Quick check before session ends |

### Performance Tracking

Each answer updates per-topic metrics. The system uses this data to:
- Recommend `WEAK_AREAS` mode when certain topics fall behind
- Inform study plan redistribution (more time on weak topics)
- Feed the Home service's today's focus recommendations

---

## 8. Flow 7: Study Plan Generation & Execution

### Overview
AI-generated day-by-day plans that distribute topics across available days. Plans respect the learner's behaviour patterns and adapt when the learner is ahead or behind.

### Generation Flow

```
┌──────────────────────┐
│ TRIGGER PLAN         │
│ GENERATION           │
│                      │
│ Two paths:           │
│ 1. Direct:           │
│    POST /study-plans │
│    {title, deadline, │
│     prepId?}         │
│                      │
│ 2. From Preparation: │
│    POST /preparations│
│    /{id}/study-plan  │
└──────────┬───────────┘
           │
           ▼
┌──────────────────────┐
│ AI PLAN GENERATION   │
│                      │
│ Inputs:              │
│ • Topics to cover    │
│ • Deadline           │
│ • Behaviour profile  │
│   (preferred times,  │
│    avg session min,  │
│    best day of week) │
│ • Existing reviews   │
│                      │
│ Outputs:             │
│ • Day-by-day items   │
│ • Interleaved SR     │
│   reviews            │
│ • Time estimates     │
└──────────┬───────────┘
           │
           ▼
┌──────────────────────┐
│ PLAN ACTIVE          │
│                      │
│ Each day shows:      │
│ • Today's items      │
│ • Status (PENDING/   │
│   COMPLETED)         │
│ • Estimated minutes  │
└──────────────────────┘
```

### Daily Execution

```
┌──────────────────────┐     Home shows plan items in todaysFocus
│ HOME: "Today's focus:│     type: "complete_plan_item"
│ Chapter 4 — Neural   │
│ Networks"            │
└──────────┬───────────┘
           │ User studies the material
           ▼
┌──────────────────────┐     POST /study-plans/{plan_id}/items/{item_id}/complete
│ MARK ITEM COMPLETE   │
└──────────┬───────────┘
           │
           ▼
┌──────────────────────┐
│ PLAN ADAPTS          │
│                      │
│ If AHEAD of schedule:│
│ → Learner is doing   │
│   well, maintain pace│
│                      │
│ If BEHIND schedule:  │
│ → Redistribute       │
│   remaining topics   │
│   across available   │
│   days               │
│ → Never exceed       │
│   sustainable session│
│   length             │
└──────────────────────┘
```

### Study Plan Features

- **Behaviour-aware**: Uses preferred study times and average session duration
- **Interleaves reviews**: Mixes spaced repetition reviews among new material
- **Adaptive**: Redistributes if learner falls behind
- **Linked to preparations**: Can be generated directly from a preparation's topics

### API Endpoints

| Action | Method | Path |
|--------|--------|------|
| Generate plan | POST | `/study-plans` |
| List active plans | GET | `/study-plans` |
| Get plan details | GET | `/study-plans/{id}` |
| Complete item | POST | `/study-plans/{id}/items/{item_id}/complete` |

---

## 9. Flow 8: Document Generation

### Overview
AI generates academic documents (essays, reports, presentations, letters, CVs) from natural language prompts. Supports PDF, DOCX, and PPTX formats with public sharing via unique URLs.

### Generation Flow

```
┌──────────────────────┐
│ USER REQUESTS DOC    │
│                      │
│ Provides:            │
│ • type (essay,       │
│   report, present-   │
│   ation, letter, cv) │
│ • title              │
│ • prompt (up to      │
│   5000 chars)        │
│ • format (pdf, docx, │
│   pptx)              │
│ • style (academic,   │
│   report, minimal)   │
└──────────┬───────────┘
           │
     ┌─────┴──────┐
     │            │
     ▼            ▼
┌─────────┐  ┌─────────────┐
│  SYNC   │  │   ASYNC     │
│ POST    │  │ POST        │
│/documents│  │/documents/  │
│         │  │ async       │
│ Blocks  │  │             │
│ until   │  │ Returns     │
│ done    │  │ taskId      │
└────┬────┘  │ immediately │
     │       └──────┬──────┘
     │              │ Poll: GET /documents/jobs/{taskId}
     │              │ status: queued → running → success
     │              │
     └──────┬───────┘
            ▼
┌──────────────────────┐
│ DOCUMENT CREATED     │
│                      │
│ • fileUrl (download) │
│ • previewUrl         │
│ • shareId (if public)│
│ • contentType        │
│ • size               │
└──────────┬───────────┘
           │ Optional
           ▼
┌──────────────────────┐     POST /documents/{id}/publish
│ PUBLISH (share)      │     Generates unique shareId
│                      │     Public access via:
│ GET /documents/share/│     /documents/share/{shareId}
│ {shareId}            │     (no auth required)
└──────────────────────┘
```

### API Endpoints

| Action | Method | Path | Auth |
|--------|--------|------|------|
| Generate (sync) | POST | `/documents` | Required |
| Generate (async) | POST | `/documents/async` | Required |
| Poll job status | GET | `/documents/jobs/{taskId}` | Required |
| List documents | GET | `/documents` | Required |
| Get document | GET | `/documents/{id}` | Required |
| Publish document | POST | `/documents/{id}/publish` | Required |
| View shared doc | GET | `/documents/share/{shareId}` | **None** |

---

## 10. Flow 9: Contextual Intelligence Chat

### Overview
The learner asks Maigie questions and gets answers personalized to their learning context. It feels like asking Maigie, not switching to a separate AI chat product.

### Chat Flow

```
┌──────────────────────┐
│ LEARNER OPENS CHAT   │  (Always available — feature "ask_maigie")
│ in Personal Learning │
│ context              │
└──────────┬───────────┘
           │
           ▼
┌──────────────────────┐     POST /api/v1/learning/chat
│ SEND MESSAGE         │     body: { message: "Explain backpropagation" }
│                      │
│ System enriches      │
│ context with:        │
│ • Current course     │
│ • Current topic      │
│ • Recent notes       │
│ • Progress data      │
│ • Goals              │
│ • Learning profile   │
│ • Proficiency level  │
│ • Preferred          │
│   explanation style  │
└──────────┬───────────┘
           │
           ▼
┌──────────────────────┐
│ AI RESPONSE          │
│                      │
│ Calibrated to:       │
│ • Learner's known    │
│   proficiency        │
│ • Preferred style    │
│   (visual, textual,  │
│    example-based)    │
│                      │
│ Ends with:           │
│ SUGGESTED NEXT       │
│ ACTION:              │
│ "Shall I add this    │
│  to your notes?"     │
│ "Ready to practice   │
│  with flashcards?"   │
└──────────────────────┘
```

### What Makes Chat Contextual

| Context Source | How It's Used |
|----------------|---------------|
| Learning profile | Calibrate explanation complexity |
| Active preparations | Reference upcoming deadlines, weak areas |
| Recent notes | Connect explanations to learner's own words |
| Behaviour patterns | Know when learner is typically focused |
| Study plan progress | Suggest what to study after the chat |
| Flashcard history | Know what concepts are being retained vs forgotten |

### Example Interactions

| User Says | System Does |
|-----------|-------------|
| "What should I study today?" | Concrete recommendation based on schedule + due reviews + plan |
| "Explain recursion" | Uses learner's proficiency (beginner → simple analogy, advanced → formal definition) |
| "Help me prepare for my exam" | References active prep, weak areas, days remaining |
| "I'm confused about Chapter 3" | Pulls notes from that chapter, offers re-explanation |

### Conversation Persistence

Conversations are stored in the existing conversation system for memory continuity across sessions.

---

## 11. Flow 10: Saved Resources (Personal Library)

### Overview
Learners bookmark and organize materials from courses, spaces, and external URLs in a personal library.

### Flow

```
┌──────────────────────┐
│ LEARNER FINDS        │
│ USEFUL RESOURCE      │
│ (article, video,     │
│  textbook, etc.)     │
└──────────┬───────────┘
           │ POST /api/v1/learning/resources
           │ body: { title, url?, sourceType, sourceId?, tags? }
           ▼
┌──────────────────────┐
│ RESOURCE SAVED       │
│                      │
│ Source types:        │
│ • course             │
│ • space              │
│ • external           │
│ • classroom          │
└──────────┬───────────┘
           │
           ▼
┌──────────────────────┐     GET /api/v1/learning/resources
│ PERSONAL LIBRARY     │     ?sourceType=external&search=python
│                      │
│ • Filter by source   │
│ • Search by title    │
│ • Paginated          │
│ • Sort by recent     │
│   access             │
└──────────────────────┘
```

### Resource Operations

| Action | Method | Path |
|--------|--------|------|
| Save resource | POST | `/resources` |
| List resources | GET | `/resources` |
| Delete resource | DELETE | `/resources/{id}` |
| Update tags | PATCH | `/resources/{id}/tags` |

### Tracking
The system tracks `lastAccessedAt` for each resource, enabling "recently used" sorting on the library view.

---

## 12. Flow 11: Behaviour & Reflection

### Behaviour Understanding

The system silently tracks study patterns to make smarter recommendations — the learner never configures preferences manually.

```
┌──────────────────────────────────────────────────────────┐
│              BEHAVIOUR TRACKING (AUTOMATIC)               │
│                                                          │
│  Study sessions start/end → system records:              │
│  • Time of day                                           │
│  • Duration                                              │
│  • Context (course, topic, activity type)                │
│  • Device                                                │
│                                                          │
│  Background analysis computes:                           │
│  ┌──────────────────────────────────────────────┐        │
│  │ BEHAVIOUR PROFILE                            │        │
│  │                                              │        │
│  │ • preferred_study_times: {morning: 60%}      │        │
│  │ • avg_session_minutes: 25                    │        │
│  │ • consistency_score: 85 (capped at 100)      │        │
│  │ • best_day_of_week: "Wednesday"              │        │
│  │ • dropout_risk: 0.15                         │        │
│  └──────────────────────────────────────────────┘        │
│                                                          │
│  Used by: Home, StudyPlan, Notifications, Proactive AI   │
└──────────────────────────────────────────────────────────┘
```

Accessible via: `GET /api/v1/learning/behaviour/profile`

### Reflections (AI-Generated Progress Summaries)

```
┌──────────────────────┐
│ LEARNER REQUESTS     │     POST /api/v1/learning/reflections/generate
│ REFLECTION           │     body: { type: "weekly" | "monthly" }
└──────────┬───────────┘
           │ Prerequisite: at least 3 days of activity
           ▼
┌──────────────────────────────────────────────────────────┐
│ AI-GENERATED REFLECTION                                  │
│                                                          │
│ Three-Layer Model:                                       │
│                                                          │
│ ┌─────────────────────────────────────────────┐          │
│ │ LAYER 1: ACTIVITIES (what you did)          │          │
│ │ "Studied 12 topics across Python and ML"    │          │
│ │ "Reviewed 45 flashcards, created 8 notes"   │          │
│ └─────────────────────────────────────────────┘          │
│                                                          │
│ ┌─────────────────────────────────────────────┐          │
│ │ LAYER 2: PROGRESS (what changed)            │          │
│ │ "Retention improved from 72% to 84%"        │          │
│ │ "Mastered 5 new concepts"                   │          │
│ └─────────────────────────────────────────────┘          │
│                                                          │
│ ┌─────────────────────────────────────────────┐          │
│ │ LAYER 3: ACHIEVEMENTS (milestones reached)  │          │
│ │ "14-day streak! Unlocked 'Consistent        │          │
│ │  Learner' badge"                            │          │
│ └─────────────────────────────────────────────┘          │
│                                                          │
│ RECOMMENDATIONS:                                         │
│ "Focus on Neural Networks next week — it's              │
│  your weakest area and your exam is in 12 days."        │
└──────────────────────────────────────────────────────────┘
```

### Reflection Endpoints

| Action | Method | Path |
|--------|--------|------|
| Generate reflection | POST | `/reflections/generate` |
| List reflections | GET | `/reflections` |
| Get specific reflection | GET | `/reflections/{id}` |

---

## 13. Flow 12: Notifications & Re-engagement

### Overview
Notifications earn the right to exist — right moment, right message. Never guilt, never pressure.

### Notification Types

| Type | Trigger | Priority | Example |
|------|---------|----------|---------|
| `review_reminder` | Due cards + near study time | Medium | "5 cards ready — 3 minutes to stay sharp" |
| `streak_protection` | No activity by usual time | Medium | "Quick review to keep your streak?" |
| `celebration` | Milestone reached | High | "14-day streak! Incredible consistency." |
| `engagement_nudge` | 3-day decline | Low | "One flashcard keeps momentum alive." |
| `plan_reminder` | Study plan item due | Medium | "Today's topic: Neural Networks" |

### Delivery Rules

```
┌──────────────────────────────────────────────────────────┐
│              NOTIFICATION DELIVERY LOGIC                  │
│                                                          │
│  1. TIMING: Deliver near learner's preferred study time  │
│     (from behaviour profile)                             │
│                                                          │
│  2. QUIET HOURS: If within quiet hours →                 │
│     queue for after quiet hours end                      │
│     (queued notifications bypass daily limit)            │
│                                                          │
│  3. DAILY LIMIT: Max 5 new notifications per day         │
│     (prevents overwhelm)                                 │
│                                                          │
│  4. PRIORITY SUPPRESSION: If multiple triggers fire      │
│     simultaneously → deliver only highest priority,      │
│     suppress others                                      │
│                                                          │
│  5. TONE: Encouraging, never guilt-inducing              │
│     ✓ "Quick review keeps knowledge fresh"               │
│     ✗ "You're falling behind!"                           │
└──────────────────────────────────────────────────────────┘
```

### Notification Flow

```
┌─────────────────┐
│ TRIGGER FIRES   │  (Celery beat task, event handler, or proactive intelligence)
└────────┬────────┘
         │
         ▼
┌─────────────────┐     Check: quiet hours? daily limit? priority?
│ DELIVERY LOGIC  │
└────────┬────────┘
         │
         ▼
┌─────────────────┐     Stored with: type, title, body, priority, actionData
│ NOTIFICATION    │
│ CREATED         │
└────────┬────────┘
         │
         ▼
┌─────────────────┐     GET /api/v1/learning/notifications
│ USER RETRIEVES  │     (sorted by priority + scheduled time)
└────────┬────────┘
         │
    ┌────┴────┐
    ▼         ▼
┌────────┐ ┌────────┐
│  READ  │ │DISMISS │
│ POST   │ │ POST   │
│ /{id}/ │ │ /{id}/ │
│ read   │ │dismiss │
└────────┘ └────────┘
```

### Proactive Intelligence (Background Tasks)

Celery beat runs these scheduled tasks:

| Task | Schedule | Action |
|------|----------|--------|
| `prepare_daily_plan` | Daily, early morning | Generates personalized daily plan |
| `check_declining_engagement` | Daily | Detects 3-day activity decline → nudge |
| `analyze_behaviour` | Daily | Recomputes behaviour profile |
| `generate_recommendations` | Daily | Refreshes discovery recommendations |
| `generate_reflections` | Weekly | Prepares weekly reflection |
| `mark_completed_preparations` | Daily | Auto-completes past-due preparations |
| `notification_delivery` | Every 15 min | Delivers queued notifications |

---

## 14. Flow 13: Discovery & Recommendations

### Overview
The system proactively recommends resources, topics, and connections based on the learner's current learning. Recommendations are pre-computed daily via background task.

### Flow

```
┌──────────────────────┐
│ BACKGROUND TASK      │  (Celery: generate_recommendations, runs daily)
│ GENERATES RECS       │
│                      │
│ Based on:            │
│ • Active goals       │
│ • Recent activity    │
│ • Existing knowledge │
│ • Complementarity    │
└──────────┬───────────┘
           │
           ▼
┌──────────────────────┐     GET /api/v1/learning/discovery
│ LEARNER VIEWS        │     Returns pre-computed recommendations
│ RECOMMENDATIONS      │
│                      │
│ Each has:            │
│ • type (resource,    │
│   topic, course)     │
│ • title              │
│ • reason             │
│ • relevanceScore     │
└──────────┬───────────┘
           │
     ┌─────┴─────┐
     │           │
     ▼           ▼
┌─────────┐ ┌─────────┐
│ FOLLOW  │ │ DISMISS │
│ POST    │ │ POST    │
│/{id}/   │ │/{id}/   │
│follow   │ │dismiss  │
│         │ │         │
│Strengthens│Weakens  │
│similar   │ │similar │
│recs      │ │recs    │
└─────────┘ └─────────┘
```

### Ranking Algorithm

Recommendations are ranked by:
1. **Relevance to current focus** — topics related to active preparations/plans
2. **Recency of need** — what the learner needs soonest
3. **Complementarity** — fills gaps in existing knowledge
4. **Learner feedback** — follows > dismissals adjust future rankings

---

## 15. Flow 14: Cross-Domain Flow (Personal ↔ Collaborative)

### Overview
Knowledge, progress, and insights flow naturally between personal learning and collaborative spaces/classrooms. The learner experiences one continuous journey.

### Cross-Domain Interactions

```
┌──────────────────────────────────────────────────────────────────┐
│                                                                  │
│  PERSONAL LEARNING                    COLLABORATIVE              │
│                                                                  │
│  ┌──────────────┐     progress sync    ┌──────────────┐         │
│  │ Study topic  │ ──────────────────▶  │ Classroom    │         │
│  │ privately    │                       │ assignment   │         │
│  └──────────────┘                       │ auto-complete│         │
│                                         └──────────────┘         │
│                                                                  │
│  ┌──────────────┐     import note       ┌──────────────┐         │
│  │ Personal     │ ──────────────────▶  │ Space note   │         │
│  │ note         │  POST /notes/{id}/    │ (copy)       │         │
│  └──────────────┘  import               └──────────────┘         │
│                                                                  │
│  ┌──────────────┐     event consumed    ┌──────────────┐         │
│  │ Home surfaces│ ◀──────────────────  │ Classroom    │         │
│  │ connection:  │  classroom.session_   │ session on   │         │
│  │ "Your class  │  ended event          │ same topic   │         │
│  │ discussed    │                       └──────────────┘         │
│  │ Binary Trees │                                                │
│  │ today"       │                                                │
│  └──────────────┘                                                │
│                                                                  │
│  ┌──────────────┐                       ┌──────────────┐         │
│  │ Spaced rep   │     context added     │ Classroom    │         │
│  │ review for   │ ◀──────────────────  │ topic match  │         │
│  │ classroom    │                       │              │         │
│  │ topic        │                       └──────────────┘         │
│  └──────────────┘                                                │
│                                                                  │
│  ┌──────────────┐                                                │
│  │ UNIFIED      │  GET /api/v1/learning/activity-feed            │
│  │ ACTIVITY     │  Shows both personal study + collaborative     │
│  │ FEED         │  activity as one continuous journey             │
│  └──────────────┘                                                │
│                                                                  │
└──────────────────────────────────────────────────────────────────┘
```

### Domain Events Consumed

| Event | Source | Personal Learning Action |
|-------|--------|-------------------------|
| `progress.streak_updated` | Progress domain | Check for streak milestones → celebration |
| `progress.achievement_unlocked` | Progress domain | Create celebration notification |
| `classroom.session_ended` | Classroom domain | Surface topic connections in Home |
| `classroom.discussion_created` | Classroom domain | Surface relevant discussions |
| `knowledge.topic_completed` | Knowledge domain | Suggest flashcard generation |

### Domain Events Emitted

| Event | When | Consumed By |
|-------|------|-------------|
| `personal_learning.note_created` | Note created | Activity feed, analytics |
| `personal_learning.topic_studied` | Topic study recorded | Progress domain |
| `personal_learning.topic_completed` | Topic marked done | Progress, SR scheduling |
| `personal_learning.quiz_completed` | Quiz finished | Performance tracking |
| `personal_learning.flashcard_reviewed` | Card reviewed | Progress, behaviour |
| `personal_learning.study_session_ended` | Session ends | Behaviour tracker |
| `personal_learning.preparation_completed` | Prep done | Celebration, analytics |
| `personal_learning.milestone_reached` | Any milestone | Notifications, achievements |
| `personal_learning.study_plan_item_completed` | Plan item done | Progress, plan adaptation |

---

## 16. Flow 15: Commercial — Trial & Capabilities

### Overview
The system supports a freemium model with feature tiers, 7-day Plus trials, conversion triggers, and educator transition paths.

### Trial Flow

```
┌──────────────────────┐
│ FREE TIER USER       │     GET /api/v1/learning/capabilities
│                      │     → Shows available/locked capabilities
│ Home may show:       │
│ premiumSuggestion    │
│ (contextual upgrade  │
│  nudge)              │
└──────────┬───────────┘
           │ User starts trial
           ▼
┌──────────────────────┐     POST /api/v1/learning/trial/start
│ 7-DAY PLUS TRIAL     │     Returns: { isActive, dayNumber, daysRemaining }
│                      │
│ All Plus features    │
│ unlocked for 7 days  │
│                      │
│ Home shows:          │
│ • trialStatus        │
│ • showcaseSuggestions │
│   (best Plus features│
│    to try)           │
└──────────┬───────────┘
           │ GET /api/v1/learning/trial/status
           │ (shows day number, remaining, suggestions)
           ▼
┌──────────────────────┐
│ TRIAL ENDS           │
│                      │
│ POST /trial/summary  │
│ → AI-generated       │
│   summary of what    │
│   Plus features the  │
│   learner used and   │
│   the value gained   │
└──────────┬───────────┘
           │
     ┌─────┴─────┐
     │           │
     ▼           ▼
┌─────────┐ ┌─────────────┐
│ UPGRADE │ │ STAY FREE   │
│ to Plus │ │             │
│         │ │ Next trial  │
│         │ │ available   │
│         │ │ after       │
│         │ │ cooldown    │
└─────────┘ └─────────────┘
```

### Home Commercial Fields

The Home response includes (when applicable):

| Field | When Shown | Content |
|-------|-----------|---------|
| `premiumSuggestion` | Free user hits a limit | `{trigger, message, capability, upgradeUrl}` |
| `trialStatus` | During trial | `{isActive, dayNumber, daysRemaining}` |
| `valueSummary` | After milestones | `{topAchievements, topFeaturesUsed}` |
| `educatorPath` | User ready to teach | `{ready, message, actionUrl}` |
| `milestone` | New milestone hit | `{milestoneId, title, shareText}` |

### Capability System

`GET /api/v1/learning/capabilities` returns:
- `effectiveTier` — Current tier (free, plus, trial)
- `isTrial` — Whether active trial
- `trialDaysRemaining` — Days left in trial
- `capabilities[]` — Each with: id, name, free/plus descriptions, user's level, locked features, upgrade value

---

## 17. Stage Progression Model

### The Guidance Engine

The Guidance Engine is the brain of autonomous learning. It replaces manual navigation with intelligent orchestration:

```
┌─────────────────────────────────────────────────────────────────────┐
│                       GUIDANCE ENGINE                                │
│                                                                     │
│  Input: Full learner state (profile, flashcards, plans, preps,     │
│         notes, behaviour, maturity)                                  │
│                                                                     │
│  Decision Flow:                                                     │
│  ┌───────────────────────────────────────────────────────────────┐  │
│  │ 1. Due flashcards exist?          → review_flashcards         │  │
│  │ 2. Today's plan items exist?      → complete_plan_item        │  │
│  │ 3. No purpose set?               → set_purpose (onboarding)  │  │
│  │ 4. Purpose set, no subjects?     → set_subjects (onboarding) │  │
│  │ 5. Setting up (auto-setup active)?→ show "preparing" state   │  │
│  │ 6. Otherwise →                    LLM decides best action     │  │
│  └───────────────────────────────────────────────────────────────┘  │
│                                                                     │
│  Output: todaysFocus with:                                          │
│  • title — what to do                                               │
│  • reason — why                                                     │
│  • estimatedMinutes — how long                                      │
│  • type — action type (maps to frontend component)                  │
│  • actionData — params needed to execute                            │
│                                                                     │
│  Plus: readyForYou[] — things the system prepared in the background │
└─────────────────────────────────────────────────────────────────────┘
```

### Feature Registry → Frontend Rendering

The FEATURE_REGISTRY defines 16 features, each with:
- `featureId` — Machine identifier
- Frontend component mapping
- Prerequisite conditions
- When it becomes relevant
- When it becomes irrelevant

The frontend uses `featureId` from guidance responses to determine which UI component to render:

| Feature ID | Frontend Component | Primary Action |
|------------|-------------------|----------------|
| `set_purpose` | `PurposeSelector` | Purpose selection screen |
| `set_subjects` | `SubjectGoalForm` | Subject/goal input |
| `create_note` | `NoteEditor` | Rich text editor |
| `create_flashcard` | `FlashcardCreator` | Front/back card form |
| `generate_flashcards_from_note` | `GenerateFlashcardsPrompt` | Confirmation + generate |
| `review_flashcards` | `FlashcardReviewSession` | Card flip + rate quality |
| `create_preparation` | `PreparationCreator` | Subject, type, date form |
| `extract_topics` | `TopicExtractionView` | Loading → topic list |
| `start_quiz` | `QuizSession` | Mode select → Q&A loop |
| `generate_study_plan` | `StudyPlanView` | Calendar/timeline view |
| `complete_plan_item` | `PlanItemCard` | "Mark Complete" button |
| `save_resource` | `ResourceSaver` | Title, URL, tags dialog |
| `generate_document` | `DocumentGenerator` | Type, title, prompt form |
| `ask_maigie` | `ChatInterface` | Chat input/output |
| `view_reflection` | `ReflectionView` | Three-layer card |
| `view_behaviour` | `BehaviourInsights` | Dashboard with patterns |

### Guidance Priority Levels

| Priority | Rendering |
|----------|-----------|
| `primary` | Large hero card with CTA button |
| `secondary` | Smaller card below, text-link style |
| `tertiary` | Text-only link in "More options" area |

---

## 18. API Endpoint Map

All endpoints mounted at `/api/v1/learning`. All require JWT authentication unless noted.

### Home & Profile

| Method | Path | Description |
|--------|------|-------------|
| GET | `/home` | Personalized home (the main entry point) |
| POST | `/onboarding/purpose` | Set learning purpose |
| POST | `/onboarding/subjects` | Set subjects and goals |
| POST | `/onboarding/complete` | Mark onboarding done |
| GET | `/profile` | Get learning profile |
| PUT | `/profile/llm-provider` | Set preferred LLM provider |

### Notes

| Method | Path | Description |
|--------|------|-------------|
| POST | `/notes` | Create note |
| GET | `/notes` | List notes (paginated, filterable) |
| GET | `/notes/{id}` | Get note |
| PATCH | `/notes/{id}` | Update note |
| DELETE | `/notes/{id}` | Delete note |
| POST | `/notes/{id}/attachments` | Add attachment |
| DELETE | `/notes/{id}/attachments/{aid}` | Remove attachment |
| POST | `/notes/{id}/summary` | Generate AI summary |
| POST | `/notes/{id}/retake` | AI rewrite with better structure |
| POST | `/notes/{id}/import` | Import to learning space |

### Preparations

| Method | Path | Description |
|--------|------|-------------|
| POST | `/preparations` | Create preparation |
| GET | `/preparations` | List preparations |
| GET | `/preparations/{id}` | Get preparation |
| PATCH | `/preparations/{id}` | Update preparation |
| DELETE | `/preparations/{id}` | Delete preparation |
| POST | `/preparations/{id}/materials` | Upload material |
| POST | `/preparations/{id}/extract-topics` | AI topic extraction |
| GET | `/preparations/{id}/topics` | List extracted topics |
| POST | `/preparations/{id}/study-plan` | Generate study plan |
| POST | `/preparations/{id}/complete` | Mark completed |

### Quizzes

| Method | Path | Description |
|--------|------|-------------|
| POST | `/preparations/{id}/quizzes` | Start quiz |
| GET | `/preparations/{id}/quizzes` | List quizzes for prep |
| POST | `/quizzes/{id}/answer` | Submit answer |
| POST | `/quizzes/{id}/complete` | Complete quiz |
| GET | `/quizzes/{id}` | Get quiz results |

### Flashcards

| Method | Path | Description |
|--------|------|-------------|
| POST | `/flashcards` | Create flashcard |
| GET | `/flashcards/due` | Get due flashcards |
| POST | `/flashcards/{id}/review` | Submit review (quality 0-5) |
| GET | `/flashcards/stats` | Get statistics |
| POST | `/flashcards/generate/note/{id}` | Generate from note |
| POST | `/flashcards/generate/topic/{id}` | Generate from topic |
| POST | `/decks` | Create deck |
| GET | `/decks` | List decks |
| GET | `/decks/{id}/flashcards` | Cards in deck |

### Study Plans

| Method | Path | Description |
|--------|------|-------------|
| POST | `/study-plans` | Generate study plan |
| GET | `/study-plans` | List active plans |
| GET | `/study-plans/{id}` | Get plan with items |
| POST | `/study-plans/{id}/items/{item_id}/complete` | Complete item |

### Documents

| Method | Path | Description |
|--------|------|-------------|
| POST | `/documents` | Generate (sync) |
| POST | `/documents/async` | Generate (async, returns taskId) |
| GET | `/documents/jobs/{taskId}` | Poll async job status |
| GET | `/documents` | List documents |
| GET | `/documents/{id}` | Get document |
| POST | `/documents/{id}/publish` | Make public (share link) |
| GET | `/documents/share/{shareId}` | View shared doc (no auth) |

### Resources

| Method | Path | Description |
|--------|------|-------------|
| POST | `/resources` | Save resource |
| GET | `/resources` | List resources |
| DELETE | `/resources/{id}` | Remove resource |
| PATCH | `/resources/{id}/tags` | Update tags |

### Notifications

| Method | Path | Description |
|--------|------|-------------|
| GET | `/notifications` | Get unread notifications |
| POST | `/notifications/{id}/read` | Mark as read |
| POST | `/notifications/{id}/dismiss` | Dismiss |

### Discovery

| Method | Path | Description |
|--------|------|-------------|
| GET | `/discovery` | Get recommendations |
| POST | `/discovery/{id}/follow` | Follow recommendation |
| POST | `/discovery/{id}/dismiss` | Dismiss recommendation |

### Behaviour & Reflections

| Method | Path | Description |
|--------|------|-------------|
| GET | `/behaviour/profile` | Get behaviour profile |
| POST | `/reflections/generate` | Generate reflection |
| GET | `/reflections` | List reflections |
| GET | `/reflections/{id}` | Get reflection |

### Chat

| Method | Path | Description |
|--------|------|-------------|
| POST | `/chat` | Send contextual message |

### Activity Feed

| Method | Path | Description |
|--------|------|-------------|
| GET | `/activity-feed` | Unified activity feed |

### Commercial

| Method | Path | Description |
|--------|------|-------------|
| GET | `/capabilities` | Feature tier + capabilities |
| POST | `/trial/start` | Start 7-day Plus trial |
| GET | `/trial/status` | Get trial status |
| POST | `/trial/summary` | Generate post-trial summary |

### Course Study

| Method | Path | Description |
|--------|------|-------------|
| GET | `/courses` | List enrolled courses with progress |
| GET | `/courses/{id}/path` | Get learning path |
| POST | `/courses/{id}/topics/{tid}/study` | Record study activity |
| POST | `/courses/{id}/topics/{tid}/complete` | Mark topic completed |

---

## Appendix: Complete User Journey (Happy Path)

This traces a learner's full journey from signup to daily engaged use:

```
DAY 0 — SIGNUP
═══════════════
1. User creates account
2. Opens Maigie → Home (stage: fresh)
3. Sees: "What brings you to Maigie today?"
4. Selects purpose: "exam_prep"
5. Enters subjects: ["Python", "Data Structures"]
6. Enters goal: "Pass my CS201 final in 30 days"
7. Auto-setup runs:
   → Creates EXAM preparation (Python - CS201)
   → Extracts topics: Arrays, Linked Lists, Trees, Graphs, Sorting, Recursion
   → Generates 12 initial flashcards
   → Creates 30-day study plan
8. Home refreshes (stage: active)
9. Today's focus: "Start with Arrays — the foundation of everything else"

DAY 1 — FIRST REAL SESSION
═══════════════════════════
1. Opens Maigie → Home shows today's plan item
2. Studies "Arrays" topic
3. Creates a note: "Array vs ArrayList — key differences"
4. Generates flashcards from note (AI creates 4 cards)
5. Takes a quick quiz (TOPIC_FOCUS on Arrays) → scores 80%
6. Marks plan item complete
7. Home updates: "Great start. Tomorrow: Linked Lists"

DAY 3 — BUILDING RHYTHM
════════════════════════
1. Opens Maigie → 6 flashcards due (highest priority)
2. Reviews flashcards (3 min), rates quality
3. SM-2 reschedules: easy cards → 4 days, hard cards → tomorrow
4. Today's plan: "Linked Lists"
5. Asks Maigie: "What's the difference between singly and doubly linked?"
6. AI responds (calibrated to beginner level, uses their notes as context)
7. Suggests: "Shall I add this to your notes?"
8. Creates note from chat
9. Behaviour tracker records: morning study, 25-min session

DAY 7 — FIRST REFLECTION
═════════════════════════
1. Opens Maigie → Home shows: "Your first week reflection is ready"
2. Views reflection:
   - Activities: Studied 5 topics, reviewed 42 flashcards, took 3 quizzes
   - Progress: Retention at 78%, mastered 8 concepts
   - Achievements: 7-day streak!
3. Recommendation: "Focus on Trees next — it builds on everything you've done"
4. Notification: "🔥 7-day streak! Incredible consistency."
5. Onboarding phase exits (activity-based: has content + purpose)

DAY 14 — DEEP ENGAGEMENT
═════════════════════════
1. Opens Maigie → 12 cards due, 2 plan items
2. Reviews flashcards first (always highest priority)
3. Quiz: WEAK_AREAS mode → surfaces Trees and Graphs (scored 55%)
4. Study plan auto-redistributes: more time on Trees this week
5. Generates study document: "Trees Cheat Sheet" (PDF)
6. Saves external resource: "Visualgo.net — algorithm visualizer"
7. Behaviour profile computed: prefers mornings, avg 25 min, consistency 90%

DAY 21 — EXAM APPROACHING
══════════════════════════
1. Opens Maigie → Today's focus: "Exam in 9 days. Focus on weak areas."
2. Quiz: FULL_PRACTICE → scores 72% overall
   - Weak: Graphs (45%), Dynamic Programming (50%)
   - Strong: Arrays (95%), Sorting (88%)
3. Study plan adapts: heavy focus on Graphs + DP for remaining days
4. Notification: "Your exam is in 9 days. You've mastered 70% of topics."
5. Chat: "Give me practice problems for graph traversal"
6. AI provides problems calibrated to their weak points

DAY 30 — EXAM DAY
══════════════════
1. Opens Maigie → "Today's the day. You've prepared well."
2. Quick review: 5 most critical flashcards
3. After exam: marks preparation as COMPLETED
4. Celebration notification: "🎉 Preparation complete!"
5. Monthly reflection available
6. System suggests: "What's next? Start a new goal or explore freely."
```

---

## Design Philosophy

> **The learner doesn't plan. They don't organize. They don't decide what's next.**
> **They open Maigie, and everything is ready. They simply learn.**

This is autonomous learning — a state where the environment handles planning, scheduling, searching, and organizing. Not without work. But without the overhead.
