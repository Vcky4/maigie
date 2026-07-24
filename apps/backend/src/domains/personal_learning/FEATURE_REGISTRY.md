# Personal Learning — Feature Registry

This document defines every capability available in the personal learning environment,
how each feature works, when it becomes relevant, and how the frontend should present it.

The Intelligence layer uses this registry to decide which features to suggest to a learner.
The frontend uses it to understand what UI components to render for each suggestion.

---

## How This Registry Works

The backend intelligence layer (LLM-driven) receives the learner's current state and this
feature registry as context. It decides which feature to suggest next based on:
- What the learner is trying to achieve (purpose, goals)
- What they've already done (existing content, activity history)
- What would create the most value right now
- What they haven't discovered yet

The backend returns a `guidance` object in the Home response. The frontend renders it
using the `featureId` to determine the correct UI component and action.

---

## Feature Definitions

### 1. SET_PURPOSE
| Property | Value |
|----------|-------|
| **ID** | `set_purpose` |
| **Name** | Set Learning Purpose |
| **Description** | Tell Maigie why you're here — exam prep, skill building, certification, or general learning |
| **Endpoint** | `POST /api/v1/learning/onboarding/purpose` |
| **Prerequisite** | None (first action for new users) |
| **Becomes irrelevant** | Once purpose is set |
| **Frontend action** | Show purpose selection screen (exam_prep, skill_building, course_completion, professional_certification, general_learning) |
| **Frontend component** | `PurposeSelector` |

---

### 2. SET_SUBJECTS
| Property | Value |
|----------|-------|
| **ID** | `set_subjects` |
| **Name** | Set Subjects & Goals |
| **Description** | Tell Maigie what you're studying and what you're working toward |
| **Endpoint** | `POST /api/v1/learning/onboarding/subjects` |
| **Prerequisite** | Purpose is set |
| **Becomes irrelevant** | Once subjects/goals are set |
| **Frontend action** | Show subject tags input + free-text goal field |
| **Frontend component** | `SubjectGoalForm` |

---

### 3. CREATE_NOTE
| Property | Value |
|----------|-------|
| **ID** | `create_note` |
| **Name** | Create a Note |
| **Description** | Write personal study notes — Maigie can summarize, rewrite, and generate flashcards from them |
| **Endpoint** | `POST /api/v1/learning/notes` |
| **Prerequisite** | None |
| **Relevant when** | Learner is studying, after completing a topic, or when exploring a subject |
| **Frontend action** | Open note editor with title + rich text content |
| **Frontend component** | `NoteEditor` |

---

### 4. CREATE_FLASHCARD
| Property | Value |
|----------|-------|
| **ID** | `create_flashcard` |
| **Name** | Create Flashcards |
| **Description** | Create question/answer cards for spaced repetition — Maigie schedules reviews automatically |
| **Endpoint** | `POST /api/v1/learning/flashcards` |
| **Prerequisite** | None |
| **Relevant when** | Learner wants to memorize concepts, after creating notes, after completing a topic |
| **Frontend action** | Show flashcard creator (front/back fields) |
| **Frontend component** | `FlashcardCreator` |

---

### 5. GENERATE_FLASHCARDS_FROM_NOTE
| Property | Value |
|----------|-------|
| **ID** | `generate_flashcards_from_note` |
| **Name** | Generate Flashcards from Note |
| **Description** | AI extracts key concepts from a note and creates flashcards automatically |
| **Endpoint** | `POST /api/v1/learning/flashcards/generate/note/{note_id}` |
| **Prerequisite** | At least one note exists with content |
| **Relevant when** | Learner just finished writing a note, or has notes without associated flashcards |
| **Frontend action** | Show confirmation with note title, then generate |
| **Frontend component** | `GenerateFlashcardsPrompt` |
| **Params** | `{ noteId: string }` |

---

### 6. REVIEW_FLASHCARDS
| Property | Value |
|----------|-------|
| **ID** | `review_flashcards` |
| **Name** | Review Due Flashcards |
| **Description** | Review flashcards that are due — rate your recall (0-5) and Maigie adjusts the schedule |
| **Endpoint** | `GET /api/v1/learning/flashcards/due` then `POST /api/v1/learning/flashcards/{id}/review` |
| **Prerequisite** | At least one flashcard exists with next_review_at <= now |
| **Relevant when** | There are due flashcards — highest urgency action |
| **Frontend action** | Open flashcard review session (flip card, rate quality) |
| **Frontend component** | `FlashcardReviewSession` |
| **Priority** | HIGH — due reviews should always be surfaced first |

---

### 7. CREATE_PREPARATION
| Property | Value |
|----------|-------|
| **ID** | `create_preparation` |
| **Name** | Start Exam/Certification Preparation |
| **Description** | Create a structured preparation for an exam, certification, interview, or project with a target date |
| **Endpoint** | `POST /api/v1/learning/preparations` |
| **Prerequisite** | None |
| **Relevant when** | Learner's purpose is exam_prep or professional_certification, or they mention an upcoming deadline |
| **Frontend action** | Show preparation creation form (subject, type, target date, description) |
| **Frontend component** | `PreparationCreator` |

---

### 8. EXTRACT_TOPICS
| Property | Value |
|----------|-------|
| **ID** | `extract_topics` |
| **Name** | Extract Topics from Materials |
| **Description** | AI analyzes uploaded materials and identifies key topics to study |
| **Endpoint** | `POST /api/v1/learning/preparations/{prep_id}/extract-topics` |
| **Prerequisite** | A preparation exists (optionally with uploaded materials) |
| **Relevant when** | Preparation has no topics yet, or materials were just uploaded |
| **Frontend action** | Show loading state while AI extracts, then display topic list |
| **Frontend component** | `TopicExtractionView` |
| **Params** | `{ prepId: string }` |

---

### 9. START_QUIZ
| Property | Value |
|----------|-------|
| **ID** | `start_quiz` |
| **Name** | Practice Quiz |
| **Description** | AI generates questions from your preparation topics — test yourself and identify weak areas |
| **Endpoint** | `POST /api/v1/learning/preparations/{prep_id}/quizzes` |
| **Prerequisite** | A preparation exists with extracted topics |
| **Relevant when** | Learner has studied topics and wants to test knowledge, or before an exam |
| **Modes** | FULL_PRACTICE (all topics), WEAK_AREAS (topics below 70%), TOPIC_FOCUS (single topic) |
| **Frontend action** | Show quiz mode selector, then run quiz (question → answer → feedback loop) |
| **Frontend component** | `QuizSession` |
| **Params** | `{ prepId: string, mode: string, topicId?: string }` |

---

### 10. GENERATE_STUDY_PLAN
| Property | Value |
|----------|-------|
| **ID** | `generate_study_plan` |
| **Name** | Generate Study Plan |
| **Description** | AI creates a day-by-day plan distributing topics until your deadline, respecting your habits |
| **Endpoint** | `POST /api/v1/learning/study-plans` |
| **Prerequisite** | A goal or preparation with a deadline |
| **Relevant when** | Learner has a deadline approaching, has topics to study, but no active plan |
| **Frontend action** | Show plan preview (calendar/timeline view of items) |
| **Frontend component** | `StudyPlanView` |
| **Params** | `{ title: string, deadline: string, prepId?: string }` |

---

### 11. COMPLETE_PLAN_ITEM
| Property | Value |
|----------|-------|
| **ID** | `complete_plan_item` |
| **Name** | Complete Today's Study Task |
| **Description** | Mark a study plan item as done — the plan adapts if you're ahead or behind |
| **Endpoint** | `POST /api/v1/learning/study-plans/{plan_id}/items/{item_id}/complete` |
| **Prerequisite** | An active study plan with pending items for today |
| **Relevant when** | There are items scheduled for today |
| **Frontend action** | Show today's task with "Mark Complete" button |
| **Frontend component** | `PlanItemCard` |
| **Params** | `{ planId: string, itemId: string }` |

---

### 12. SAVE_RESOURCE
| Property | Value |
|----------|-------|
| **ID** | `save_resource` |
| **Name** | Save a Resource |
| **Description** | Bookmark useful materials (textbooks, articles, videos) to your personal library |
| **Endpoint** | `POST /api/v1/learning/resources` |
| **Prerequisite** | None |
| **Relevant when** | Learner discovers useful external content |
| **Frontend action** | Show save dialog (title, URL, source type, tags) |
| **Frontend component** | `ResourceSaver` |

---

### 13. GENERATE_DOCUMENT
| Property | Value |
|----------|-------|
| **ID** | `generate_document` |
| **Name** | Generate Academic Document |
| **Description** | AI generates essays, reports, or presentations in PDF/DOCX/PPTX format |
| **Endpoint** | `POST /api/v1/learning/documents` |
| **Prerequisite** | None |
| **Relevant when** | Learner needs to produce a document for an assignment or wants study summaries |
| **Frontend action** | Show document generation form (type, title, prompt, format) |
| **Frontend component** | `DocumentGenerator` |

---

### 14. ASK_MAIGIE
| Property | Value |
|----------|-------|
| **ID** | `ask_maigie` |
| **Name** | Ask Maigie |
| **Description** | Ask any learning question — Maigie knows your context, goals, and progress |
| **Endpoint** | `POST /api/v1/learning/chat` |
| **Prerequisite** | None (always available) |
| **Relevant when** | Always — but especially when learner is stuck, confused, or needs guidance |
| **Frontend action** | Open chat interface |
| **Frontend component** | `ChatInterface` |

---

### 15. VIEW_REFLECTION
| Property | Value |
|----------|-------|
| **ID** | `view_reflection` |
| **Name** | Weekly Reflection |
| **Description** | AI-generated summary of your learning progress — what you did, what changed, what to focus on next |
| **Endpoint** | `POST /api/v1/learning/reflections/generate` |
| **Prerequisite** | At least 3 days of activity |
| **Relevant when** | End of week, or learner hasn't reflected recently |
| **Frontend action** | Show reflection card with three layers (activities, progress, achievements) |
| **Frontend component** | `ReflectionView` |

---

### 16. VIEW_BEHAVIOUR
| Property | Value |
|----------|-------|
| **ID** | `view_behaviour` |
| **Name** | Study Patterns |
| **Description** | See when you study best, your consistency score, and session patterns |
| **Endpoint** | `GET /api/v1/learning/behaviour/profile` |
| **Prerequisite** | At least 5 study sessions recorded |
| **Relevant when** | Learner has established patterns, or wants to understand their habits |
| **Frontend action** | Show behaviour dashboard (time distribution, consistency score, best day) |
| **Frontend component** | `BehaviourInsights` |

---

## Frontend Guidance Contract

The Home endpoint returns a `guidance` field (replacing the binary `isOnboarding`):

```json
{
  "guidance": {
    "message": "You've set up your exam preparation. Let's extract the key topics from your subject so I can build a study plan.",
    "suggestedFeature": {
      "featureId": "extract_topics",
      "title": "Extract Study Topics",
      "description": "AI will identify the key topics you need to cover",
      "actionData": { "prepId": "abc123" },
      "priority": "primary"
    },
    "alternativeFeatures": [
      {
        "featureId": "create_note",
        "title": "Write a study note instead",
        "priority": "secondary"
      },
      {
        "featureId": "ask_maigie",
        "title": "Ask me anything",
        "priority": "tertiary"
      }
    ]
  }
}
```

### Frontend Rendering Rules

| Priority | Rendering |
|----------|-----------|
| `primary` | Large card with CTA button, prominent placement |
| `secondary` | Smaller card below primary, text link style |
| `tertiary` | Text-only link in a "More options" section |

### Feature Action Types

The frontend uses `featureId` to determine which component to mount and what data to pass:

```typescript
interface GuidanceSuggestion {
  featureId: string;        // Maps to a known feature from this registry
  title: string;            // Human-readable action title
  description?: string;     // Optional explanation
  actionData?: Record<string, any>;  // Feature-specific params (prepId, noteId, etc.)
  priority: 'primary' | 'secondary' | 'tertiary';
}
```

---

## Intelligence Context (what the LLM receives)

When computing guidance, the LLM receives:

1. **Learner state:**
   - Purpose, subjects, goals
   - What content exists (note count, flashcard count, prep count, plan count)
   - Due reviews count
   - Days since signup
   - Last activity type and timestamp
   - Active preparation (if any) with topics and mastery scores

2. **Feature registry** (this document, condensed)

3. **Instruction:**
   "Based on the learner's current state, suggest the single most valuable next action.
   Consider what would create the most learning progress right now.
   If they have due flashcards, that's always highest priority.
   Otherwise, guide them toward the next logical step in their journey.
   Never suggest features they've already completed (e.g., don't suggest set_purpose if purpose exists).
   Return your suggestion as a JSON object with message, suggestedFeature, and alternativeFeatures."

---

## Feature Relevance Matrix (Quick Reference)

| Feature | Relevant When |
|---------|---------------|
| `set_purpose` | No purpose set |
| `set_subjects` | Purpose set, no subjects |
| `create_note` | Always (after purpose) |
| `create_flashcard` | Always (after purpose) |
| `generate_flashcards_from_note` | Notes exist without flashcards |
| `review_flashcards` | Due flashcards exist (HIGHEST PRIORITY) |
| `create_preparation` | Purpose is exam/cert, no active prep |
| `extract_topics` | Prep exists, no topics |
| `start_quiz` | Topics exist with mastery data |
| `generate_study_plan` | Deadline exists, no active plan |
| `complete_plan_item` | Plan items scheduled for today |
| `save_resource` | Always (low priority, contextual) |
| `generate_document` | Assignment-type purpose or explicit need |
| `ask_maigie` | Always (ambient, not primary suggestion) |
| `view_reflection` | 3+ days of activity, end of week |
| `view_behaviour` | 5+ study sessions recorded |
