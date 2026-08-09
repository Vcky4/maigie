# Backend Onboarding Implementation

**Status**: ✅ Code Complete (migration pending database access)  
**Date**: August 9, 2026

---

## Summary

Implemented unified onboarding backend according to the specification in `UNIFIED_ONBOARDING_SPEC.md`. The backend now supports purpose-specific detail collection and state machine tracking.

---

## Changes Made

### 1. Database Migration

**File**: `alembic/versions/015_add_onboarding_state_fields.py`

Added 5 new columns to `LearningProfile` table:

```sql
onboardingState VARCHAR NOT NULL DEFAULT 'not_started'
examName VARCHAR NULL
examDate DATE NULL
skillName VARCHAR NULL
currentLevel VARCHAR NULL
```

**Backfill Logic**:
- Existing profiles with `onboardingCompletedAt` → `'completed'`
- Profiles with `purpose` but no completion → `'purpose_set'`
- Everything else → `'not_started'`

### 2. Data Models

**File**: `src/domains/personal_learning/db_models.py`

Updated `LearningProfile` SQLAlchemy model:

```python
onboarding_state: Mapped[str] = mapped_column(
    "onboardingState", String, nullable=False, 
    default="not_started", server_default="not_started"
)
exam_name: Mapped[str | None] = mapped_column("examName", String, nullable=True)
exam_date: Mapped[date | None] = mapped_column("examDate", Date, nullable=True)
skill_name: Mapped[str | None] = mapped_column("skillName", String, nullable=True)
current_level: Mapped[str | None] = mapped_column("currentLevel", String, nullable=True)
```

**File**: `src/domains/personal_learning/models.py`

Added new types and request/response models:

```python
# New Enums
OnboardingState = Literal[
    "not_started",
    "purpose_set",
    "details_set",
    "content_ready",
    "completed",
]

SkillLevel = Literal["beginner", "intermediate", "advanced"]

# Enhanced LearningPurpose
LearningPurpose = Literal[
    "exam_prep",
    "skill_building",
    "course_completion",
    "professional_certification",
    "teaching",        # NEW
    "community",       # NEW
    "general_learning",
]

# New Request Models
class ExamDetailsRequest(CamelModel):
    exam_name: str
    exam_date: date | None
    subjects: list[str]
    goals: str | None

class SkillDetailsRequest(CamelModel):
    skill_name: str
    current_level: SkillLevel | None
    subjects: list[str]
    goals: str | None

# New Response Model
class OnboardingStatusResponse(CamelModel):
    state: OnboardingState
    progress: dict[str, bool]
    estimated_seconds_remaining: int | None
    first_preparation: dict[str, str] | None
```

Updated `LearningProfileResponse` to include new fields:

```python
onboarding_state: OnboardingState = "not_started"
exam_name: str | None = None
exam_date: date | None = None
skill_name: str | None = None
current_level: SkillLevel | None = None
```

### 3. Service Layer

**File**: `src/domains/personal_learning/services/onboarding_service.py`

#### Modified Functions

**`set_purpose()`** - Enhanced to set onboarding_state:

```python
async def set_purpose(*, user_id: str, purpose: str) -> Any:
    # ... existing logic ...
    return await repo.update_profile(
        user_id, {"purpose": purpose, "onboardingState": "purpose_set"}
    )
```

**`complete_onboarding()`** - Updates state to completed:

```python
async def complete_onboarding(*, user_id: str) -> None:
    await IdentityRepository().set_onboarded(user_id)
    await repo.update_profile(
        user_id,
        {
            "onboardingCompletedAt": datetime.now(UTC),
            "onboardingState": "completed",
        },
    )
```

#### New Functions

**`set_exam_details()`** - Exam prep specific onboarding:

```python
async def set_exam_details(
    *,
    user_id: str,
    exam_name: str,
    exam_date: date | None = None,
    subjects: list[str] | None = None,
    goals: str | None = None,
) -> Any:
    """Set exam preparation details and trigger content generation."""
    profile = await repo.update_profile(user_id, {
        "examName": exam_name,
        "examDate": exam_date,
        "subjects": subjects,
        "goalsText": goals,
        "onboardingState": "details_set",
    })
    
    # Trigger background content generation
    asyncio.create_task(_generate_onboarding_content(...))
    
    return profile
```

**`set_skill_details()`** - Skill building specific onboarding:

```python
async def set_skill_details(
    *,
    user_id: str,
    skill_name: str,
    current_level: str | None = None,
    subjects: list[str] | None = None,
    goals: str | None = None,
) -> Any:
    """Set skill building details and trigger content generation."""
    profile = await repo.update_profile(user_id, {
        "skillName": skill_name,
        "currentLevel": current_level,
        "subjects": subjects,
        "goalsText": goals,
        "onboardingState": "details_set",
    })
    
    # Trigger background content generation
    asyncio.create_task(_generate_onboarding_content(...))
    
    return profile
```

**`get_onboarding_status()`** - Status polling endpoint:

```python
async def get_onboarding_status(*, user_id: str) -> dict[str, Any]:
    """Get current onboarding status for progress polling."""
    profile = await repo.get_profile_by_user(user_id)
    preps = await repo.list_exam_preps(user_id)
    
    return {
        "state": profile.onboarding_state or "not_started",
        "progress": {
            "preparation": len(preps) > 0,
            "topics": False,  # TODO: implement checks
            "flashcards": False,
            "studyPlan": False,
        },
        "estimatedSecondsRemaining": 30 if state == "details_set" else 0,
        "firstPreparation": {
            "id": preps[0].id,
            "subject": preps[0].subject
        } if preps else None
    }
```

**`_generate_onboarding_content()`** - Background content generation:

```python
async def _generate_onboarding_content(...) -> None:
    """
    Generate initial content for a new learner.
    
    Should be a Celery task (TODO), currently runs as async task.
    """
    try:
        await auto_setup_service.auto_setup_for_learner(user_id=user_id)
        await repo.update_profile(user_id, {"onboardingState": "content_ready"})
    except Exception as e:
        logger.error(f"Content generation failed: {e}")
        # Don't update state - keeps user in details_set
```

### 4. API Routes

**File**: `src/domains/personal_learning/routes.py`

#### New Endpoints

```python
@router.post("/onboarding/exam-details", response_model=models.LearningProfileResponse)
async def set_exam_details(body: models.ExamDetailsRequest, current_user: CurrentUser):
    """Set exam preparation details. For EXAM_PREP purpose learners."""
    return await onboarding_service.set_exam_details(
        user_id=current_user.id,
        exam_name=body.exam_name,
        exam_date=body.exam_date,
        subjects=body.subjects,
        goals=body.goals,
    )

@router.post("/onboarding/skill-details", response_model=models.LearningProfileResponse)
async def set_skill_details(body: models.SkillDetailsRequest, current_user: CurrentUser):
    """Set skill building details. For SKILL_BUILDING purpose learners."""
    return await onboarding_service.set_skill_details(
        user_id=current_user.id,
        skill_name=body.skill_name,
        current_level=body.current_level,
        subjects=body.subjects,
        goals=body.goals,
    )

@router.get("/onboarding/status", response_model=models.OnboardingStatusResponse)
async def get_onboarding_status(current_user: CurrentUser):
    """Get current onboarding status for progress polling."""
    return await onboarding_service.get_onboarding_status(user_id=current_user.id)
```

#### Deprecated Endpoint

```python
@router.post("/onboarding/subjects", response_model=models.LearningProfileResponse)
async def set_subjects(body: models.SubjectsSetRequest, current_user: CurrentUser):
    """
    DEPRECATED: Use /onboarding/exam-details or /onboarding/skill-details instead.
    Kept for backward compatibility.
    """
    return await onboarding_service.set_subjects(...)
```

---

## API Contract

### Set Purpose

```http
POST /api/v1/learning/onboarding/purpose
Authorization: Bearer {access_token}
Content-Type: application/json

{
  "purpose": "exam_prep"
}

Response 201:
{
  "id": "prof_123",
  "userId": "user_456",
  "onboardingState": "purpose_set",
  "purpose": "exam_prep",
  "createdAt": "2026-08-09T12:00:00Z",
  "updatedAt": "2026-08-09T12:00:00Z"
}
```

### Set Exam Details

```http
POST /api/v1/learning/onboarding/exam-details
Authorization: Bearer {access_token}
Content-Type: application/json

{
  "examName": "SAT",
  "examDate": "2027-03-15",
  "subjects": ["Math", "Reading", "Writing"],
  "goals": "Score 1500+ for college applications"
}

Response 200:
{
  "id": "prof_123",
  "userId": "user_456",
  "onboardingState": "details_set",
  "purpose": "exam_prep",
  "examName": "SAT",
  "examDate": "2027-03-15",
  "subjects": ["Math", "Reading", "Writing"],
  "goalsText": "Score 1500+ for college applications"
}
```

### Set Skill Details

```http
POST /api/v1/learning/onboarding/skill-details
Authorization: Bearer {access_token}
Content-Type: application/json

{
  "skillName": "Python Programming",
  "currentLevel": "beginner",
  "subjects": ["Data Structures", "Django", "Testing"],
  "goals": "Build full-stack web applications"
}

Response 200:
{
  "id": "prof_123",
  "userId": "user_456",
  "onboardingState": "details_set",
  "purpose": "skill_building",
  "skillName": "Python Programming",
  "currentLevel": "beginner",
  "subjects": ["Data Structures", "Django", "Testing"]
}
```

### Get Onboarding Status

```http
GET /api/v1/learning/onboarding/status
Authorization: Bearer {access_token}

Response 200:
{
  "state": "details_set",
  "progress": {
    "preparation": false,
    "topics": false,
    "flashcards": false,
    "studyPlan": false
  },
  "estimatedSecondsRemaining": 30,
  "firstPreparation": null
}
```

After content generation completes:

```http
GET /api/v1/learning/onboarding/status

Response 200:
{
  "state": "content_ready",
  "progress": {
    "preparation": true,
    "topics": true,
    "flashcards": true,
    "studyPlan": true
  },
  "estimatedSecondsRemaining": 0,
  "firstPreparation": {
    "id": "prep_789",
    "subject": "SAT"
  }
}
```

### Complete Onboarding

```http
POST /api/v1/learning/onboarding/complete
Authorization: Bearer {access_token}

Response 204 No Content
```

Side effects:
- Sets `User.is_onboarded = true`
- Sets `LearningProfile.onboarding_state = 'completed'`
- Records `LearningProfile.onboarding_completed_at`

---

## State Machine

```
not_started
    ↓ POST /onboarding/purpose
purpose_set
    ↓ POST /onboarding/exam-details OR /onboarding/skill-details
details_set
    ↓ background: _generate_onboarding_content()
content_ready
    ↓ POST /onboarding/complete
completed
```

**States**:

- `not_started` - No profile exists or profile has no purpose
- `purpose_set` - Purpose chosen, awaiting details
- `details_set` - Context collected, content generating
- `content_ready` - Content generated, ready to complete
- `completed` - `is_onboarded = true`, onboarding done

**Recovery**:
- User can close app at any state and resume where they left off
- State persisted in database, not local storage
- Web and mobile can query `/status` to determine current step

---

## TODO / Future Improvements

### Background Jobs (High Priority)

Currently `_generate_onboarding_content()` runs as `asyncio.create_task()` which is **not production-ready**:

```python
# CURRENT (BAD):
asyncio.create_task(_generate_onboarding_content(...))

# SHOULD BE (CELERY):
from src.shared.celery import celery_app

@celery_app.task
def generate_onboarding_content_task(user_id: str, ...):
    asyncio.run(_generate_onboarding_content(user_id, ...))

# Then in service:
generate_onboarding_content_task.delay(user_id, ...)
```

**Reasons why current approach is bad**:
1. Task dies if web server restarts
2. No retry mechanism on failure
3. No monitoring/visibility into job status
4. Ties up async event loop with long-running AI calls

**Action**: Set up Celery + Redis before production deploy

### Progress Tracking (Medium Priority)

The `progress` dict in `/onboarding/status` is currently hardcoded:

```python
progress = {
    "preparation": first_prep is not None,
    "topics": False,  # TODO
    "flashcards": False,  # TODO
    "studyPlan": False,  # TODO
}
```

**Action**: Implement actual checks:
- Count topics in `Topic` table for user
- Count flashcards in `Flashcard` table
- Check `StudyPlan` table for active plan

### Time Estimation (Low Priority)

Estimated time is currently a rough heuristic:

```python
if state == "details_set":
    estimated_seconds = 30
```

**Action**: Track actual generation time and use moving average

### Teaching/Community Paths (Medium Priority)

Currently spec says teaching/community redirect to space creation, but:
- Space creation isn't part of personal_learning domain
- Should we integrate `LearningProfile` with spaces?
- Or treat teaching path differently (no profile created)?

**Decision needed**: How do educator-focused users interact with personal learning?

---

## Testing Checklist

When database becomes accessible:

- [ ] Run migration: `poetry run alembic upgrade head`
- [ ] Verify new columns exist in `LearningProfile` table
- [ ] Test signup → purpose → exam details → status → complete flow
- [ ] Test signup → purpose → skill details → status → complete flow
- [ ] Test interrupted flow (close after purpose, resume at details)
- [ ] Test backward compatibility (old `/onboarding/subjects` still works)
- [ ] Test OAuth flow → onboarding → complete
- [ ] Verify `is_onboarded` flag set correctly
- [ ] Test cross-device (start web, finish mobile) - requires deployed env

---

## Migration Instructions

### When Database Access Available

1. Run migration:
   ```bash
   cd apps/backend
   poetry run alembic upgrade head
   ```

2. Verify schema:
   ```sql
   \d+ "LearningProfile"
   ```
   
   Should show: `onboardingState`, `examName`, `examDate`, `skillName`, `currentLevel`

3. Check backfill:
   ```sql
   SELECT "onboardingState", COUNT(*) 
   FROM "LearningProfile" 
   GROUP BY "onboardingState";
   ```

4. Test new endpoints:
   ```bash
   # Get auth token first
   TOKEN=$(curl -X POST http://localhost:8000/api/v1/auth/login/json \
     -H "Content-Type: application/json" \
     -d '{"email":"test@example.com","password":"password"}' \
     | jq -r '.access_token')
   
   # Test purpose
   curl -X POST http://localhost:8000/api/v1/learning/onboarding/purpose \
     -H "Authorization: Bearer $TOKEN" \
     -H "Content-Type: application/json" \
     -d '{"purpose":"exam_prep"}'
   
   # Test exam details
   curl -X POST http://localhost:8000/api/v1/learning/onboarding/exam-details \
     -H "Authorization: Bearer $TOKEN" \
     -H "Content-Type: application/json" \
     -d '{
       "examName":"SAT",
       "examDate":"2027-03-15",
       "subjects":["Math","Reading"],
       "goals":"Score 1500+"
     }'
   
   # Poll status
   curl -X GET http://localhost:8000/api/v1/learning/onboarding/status \
     -H "Authorization: Bearer $TOKEN"
   ```

### Rollback Plan

If migration causes issues:

```bash
poetry run alembic downgrade -1
```

This will:
- Drop the 5 new columns
- Restore `LearningProfile` to previous state
- **WARNING**: Loses any exam/skill details collected after upgrade

---

## Files Changed

1. `alembic/versions/015_add_onboarding_state_fields.py` - NEW
2. `src/domains/personal_learning/db_models.py` - Modified `LearningProfile`
3. `src/domains/personal_learning/models.py` - Added enums and request/response models
4. `src/domains/personal_learning/services/onboarding_service.py` - Added 4 new functions
5. `src/domains/personal_learning/routes.py` - Added 3 new endpoints

---

## Next Steps

1. ✅ Backend implementation complete
2. ⏭️ Update mobile UI to match web flow
3. ⏭️ Test end-to-end on both platforms
4. ⏭️ Set up Celery for background jobs (before production)
