# Unified Onboarding Flow Specification

**Version**: 1.0  
**Date**: August 2026  
**Status**: Design Document

---

## Overview

This document specifies a unified onboarding experience across web and mobile platforms. The goal is to provide a consistent, purpose-first flow that quickly gets users to their personalized learning environment.

### Design Principles

1. **Purpose First** - Ask "What brings you here?" before collecting details
2. **Progressive Disclosure** - Collect only essential information, defer optional details
3. **Fast Time-to-Value** - Show initial content within 30 seconds of signup
4. **Platform Consistency** - Same steps, same questions, same visual language
5. **Recoverable** - Users can resume if interrupted

---

## User Journey

### Stage 1: Authentication (Identity Domain)

**Goal**: Establish identity and verify ownership

#### Email/Password Flow

```
┌──────────────┐
│   Signup     │ → Enter name, email, password
└──────┬───────┘
       │
       ↓
┌──────────────┐
│  Verify OTP  │ → 6-digit code sent to email
└──────┬───────┘
       │
       ↓
┌──────────────┐
│   Activated  │ → User.is_active = true
└──────────────┘
```

**Web**: Separate pages (`/signup` → `/verify-otp`)  
**Mobile**: Separate screens (`AuthScreen` → `VerifyOtpScreen`)

#### OAuth Flow

```
┌──────────────┐
│ Google Auth  │ → Native SDK (mobile) or redirect (web)
└──────┬───────┘
       │
       ↓
┌──────────────┐
│  Auto Active │ → User created with is_active = true
└──────────────┘
```

**No OTP required** - Google verifies email ownership

#### Completion Criteria
- `User.is_active = true`
- `User.is_onboarded = false`
- User has valid access_token

---

### Stage 2: Purpose Discovery (Personal Learning Domain)

**Goal**: Understand user's primary intention

#### Screen: "What brings you to Maigie?"

**Options** (5 identity paths):

1. **Prepare for exams** 🎓
   - Purpose: `EXAM_PREP`
   - Next: Exam details form
   
2. **Learn a skill** 💼
   - Purpose: `SKILL_BUILDING`
   - Next: Subjects form
   
3. **Teach or mentor** 🎤
   - Purpose: `TEACHING` (new enum value)
   - Next: Space creation (different flow)
   
4. **Create a community** 🏢
   - Purpose: `COMMUNITY` (new enum value)
   - Next: Space creation (different flow)
   
5. **Join a Learning Space** 🚪
   - Purpose: `GENERAL_LEARNING`
   - Next: Pending invites list

#### API Call
```http
POST /api/v1/learning/onboarding/purpose
Content-Type: application/json

{
  "purpose": "EXAM_PREP" | "SKILL_BUILDING" | "TEACHING" | "COMMUNITY" | "GENERAL_LEARNING"
}
```

**Response**:
```json
{
  "id": "profile_id",
  "userId": "user_id",
  "purpose": "EXAM_PREP",
  "onboardingState": "purpose_set",
  "createdAt": "2026-08-09T12:00:00Z"
}
```

#### Completion Criteria
- `Learning_Profile.purpose` set
- `Learning_Profile.onboarding_state = 'purpose_set'`

---

### Stage 3: Context Collection (Purpose-Specific)

**Goal**: Gather minimum context to generate useful content

#### For Exam Prep

**Form Fields**:
- Exam name (required) - e.g., "SAT", "MCAT", "IELTS"
- Exam date (optional) - target date for timeline
- Subjects/topics (optional) - e.g., ["Math", "Reading", "Writing"]
- Study goals (optional) - free text

**API Call**:
```http
POST /api/v1/learning/onboarding/exam-details
Content-Type: application/json

{
  "examName": "SAT",
  "examDate": "2027-03-15",
  "subjects": ["Math", "Reading", "Writing"],
  "goals": "Score 1500+ for college applications"
}
```

#### For Skill Building

**Form Fields**:
- Skill/subject (required) - e.g., "Python Programming"
- Current level (optional) - "Beginner", "Intermediate", "Advanced"
- Subjects/topics (optional) - e.g., ["Data Structures", "Web Development"]
- Learning goals (optional) - free text

**API Call**:
```http
POST /api/v1/learning/onboarding/skill-details
Content-Type: application/json

{
  "skillName": "Python Programming",
  "currentLevel": "Beginner",
  "subjects": ["Data Structures", "Web Development", "Django"],
  "goals": "Build full-stack web applications"
}
```

#### For Teaching (Space Creator)

**Redirect**: Navigate to space creation flow (existing)
- Space creation is a separate domain concern
- On space created → mark onboarding complete
- Skip content generation (teachers build their own)

#### For Community (Space Creator)

**Redirect**: Same as teaching, navigate to space creation

#### For Join Space (Space Member)

**Redirect**: Show pending invites
- On invite accepted → mark onboarding complete
- Inherit space's structure, skip personal content

---

### Stage 4: Content Generation (Background)

**Goal**: Create personalized learning environment

#### Backend Process

```python
async def generate_onboarding_content(user_id: str, details: dict):
    """
    Generate initial content based on onboarding details.
    Runs as background job to avoid blocking the user.
    """
    # 1. Create Preparation
    prep = await create_preparation(
        user_id=user_id,
        subject=details['examName'] or details['skillName'],
        prep_type='EXAM' if details.get('examDate') else 'GENERAL',
        target_date=details.get('examDate')
    )
    
    # 2. Extract Topics via AI
    topics = await extract_topics_from_context(
        subjects=details.get('subjects', []),
        goals=details.get('goals'),
        prep_id=prep.id
    )
    
    # 3. Generate Initial Flashcards
    for topic in topics[:3]:  # First 3 topics only
        await generate_flashcards_for_topic(topic.id, count=5)
    
    # 4. Build Study Plan
    plan = await generate_study_plan(
        prep_id=prep.id,
        target_date=details.get('examDate'),
        topics=topics
    )
    
    # 5. Update state
    await update_onboarding_state(user_id, 'content_ready')
```

**Status Endpoint**:
```http
GET /api/v1/learning/onboarding/status

Response:
{
  "state": "generating_content" | "content_ready" | "completed",
  "progress": {
    "preparation": true,
    "topics": true,
    "flashcards": false,
    "studyPlan": false
  },
  "estimatedSecondsRemaining": 15
}
```

#### Web/Mobile: Progress Screen

Shows animated progress:
- "Creating your preparation..."
- "Extracting key topics..."
- "Generating practice flashcards..."
- "Building your study plan..."

Polls `/onboarding/status` every 2s until `state = 'content_ready'`

---

### Stage 5: Completion

**Goal**: Mark onboarding complete and route to workspace

#### API Call
```http
POST /api/v1/learning/onboarding/complete
```

**Backend Actions**:
- Set `User.is_onboarded = true`
- Set `Learning_Profile.onboarding_state = 'completed'`
- Record `Learning_Profile.onboarding_completed_at`

#### Navigation

**Web**: 
- Redirect to `/home` or `/prep/{prep_id}` (first preparation)

**Mobile**:
- Navigate to `/today` or `/studio/workspace/{courseId}/{moduleId}/{topicId}` (first topic)

---

## Data Models

### Learning_Profile (Enhanced)

```python
class LearningProfile:
    id: str
    user_id: str
    purpose: LearningPurpose  # enum
    onboarding_state: OnboardingState  # NEW - tracks progress
    onboarding_completed_at: datetime | None
    
    # Context fields (set during onboarding)
    exam_name: str | None  # NEW
    exam_date: date | None  # NEW
    skill_name: str | None  # NEW
    current_level: str | None  # NEW
    subjects: list[str]
    goals_text: str | None
    
    # Existing fields
    maturity_days: int
    preferred_llm_provider: str | None
    quiet_hours_start: str | None
    quiet_hours_end: str | None
```

### OnboardingState (New Enum)

```python
class OnboardingState(str, Enum):
    NOT_STARTED = "not_started"        # Profile doesn't exist yet
    PURPOSE_SET = "purpose_set"        # Purpose chosen, awaiting details
    DETAILS_SET = "details_set"        # Context collected, content generating
    CONTENT_READY = "content_ready"    # Content generated, ready to complete
    COMPLETED = "completed"            # is_onboarded = true
```

### LearningPurpose (Enhanced)

```python
class LearningPurpose(str, Enum):
    EXAM_PREP = "EXAM_PREP"
    SKILL_BUILDING = "SKILL_BUILDING"
    COURSE_COMPLETION = "COURSE_COMPLETION"
    PROFESSIONAL_CERTIFICATION = "PROFESSIONAL_CERTIFICATION"
    TEACHING = "TEACHING"              # NEW
    COMMUNITY = "COMMUNITY"            # NEW
    GENERAL_LEARNING = "GENERAL_LEARNING"
```

---

## API Contract

### New/Modified Endpoints

#### 1. Set Purpose (Modified)

```http
POST /api/v1/learning/onboarding/purpose
Content-Type: application/json

{
  "purpose": "EXAM_PREP"
}

Response 201:
{
  "id": "prof_123",
  "userId": "user_456",
  "purpose": "EXAM_PREP",
  "onboardingState": "purpose_set",
  "createdAt": "2026-08-09T12:00:00Z"
}
```

#### 2. Set Exam Details (New)

```http
POST /api/v1/learning/onboarding/exam-details
Content-Type: application/json

{
  "examName": "SAT",
  "examDate": "2027-03-15",
  "subjects": ["Math", "Reading"],
  "goals": "Score 1500+"
}

Response 200:
{
  "id": "prof_123",
  "purpose": "EXAM_PREP",
  "onboardingState": "details_set",
  "examName": "SAT",
  "examDate": "2027-03-15",
  "subjects": ["Math", "Reading"]
}
```

**Backend**: Triggers background job `generate_onboarding_content.delay(user_id, details)`

#### 3. Set Skill Details (New)

```http
POST /api/v1/learning/onboarding/skill-details
Content-Type: application/json

{
  "skillName": "Python Programming",
  "currentLevel": "Beginner",
  "subjects": ["Data Structures", "Django"],
  "goals": "Build web apps"
}

Response 200:
{
  "id": "prof_123",
  "purpose": "SKILL_BUILDING",
  "onboardingState": "details_set",
  "skillName": "Python Programming",
  "currentLevel": "Beginner"
}
```

#### 4. Get Onboarding Status (New)

```http
GET /api/v1/learning/onboarding/status

Response 200:
{
  "state": "generating_content",
  "progress": {
    "preparation": true,
    "topics": true,
    "flashcards": false,
    "studyPlan": false
  },
  "estimatedSecondsRemaining": 12,
  "firstPreparation": {
    "id": "prep_789",
    "subject": "SAT"
  }
}
```

#### 5. Complete Onboarding (Modified)

```http
POST /api/v1/learning/onboarding/complete

Response 204 No Content
```

**Backend**: Sets `is_onboarded = true`, records completion time

#### 6. Deprecated: Set Subjects

```http
POST /api/v1/learning/onboarding/subjects
```

**Status**: Keep for backward compatibility, but deprecate in favor of `/exam-details` or `/skill-details`

---

## Mobile UI Components

### Purpose Selection Screen

```tsx
// Replace conversational onboarding with this form-based screen
<OnboardingPurposeScreen>
  <Header>
    <Logo />
    <ProgressBar current={1} total={3} />
  </Header>
  
  <WelcomeText>
    Welcome, {firstName}.
    Where should we begin?
  </WelcomeText>
  
  <PurposeOptions>
    <PurposeCard
      icon={GraduationCap}
      title="Prepare for exams"
      description="Build a focused study path around your syllabus"
      accent="violet"
      onPress={() => selectPurpose('EXAM_PREP')}
    />
    {/* ... other options ... */}
  </PurposeOptions>
</OnboardingPurposeScreen>
```

### Details Form Screen (Exam Prep Example)

```tsx
<OnboardingExamDetailsScreen>
  <Header>
    <BackButton onPress={goBack} />
    <ProgressBar current={2} total={3} />
  </Header>
  
  <Form>
    <TextInput
      label="Exam Name"
      placeholder="e.g., SAT, MCAT, IELTS"
      required
      value={examName}
      onChange={setExamName}
    />
    
    <DatePicker
      label="Target Exam Date"
      placeholder="When are you taking the exam?"
      value={examDate}
      onChange={setExamDate}
    />
    
    <SubjectTagInput
      label="Subjects or Topics"
      placeholder="Add subjects you'll study"
      tags={subjects}
      onAdd={addSubject}
      onRemove={removeSubject}
    />
    
    <TextArea
      label="Study Goals (Optional)"
      placeholder="What score are you aiming for? Why is this exam important?"
      value={goals}
      onChange={setGoals}
    />
    
    <PrimaryButton
      onPress={handleSubmit}
      disabled={!examName}
    >
      Continue
    </PrimaryButton>
  </Form>
</OnboardingExamDetailsScreen>
```

### Progress Screen

```tsx
<OnboardingProgressScreen>
  <AnimatedCheckmarks>
    <Step completed={progress.preparation}>
      Creating your preparation
    </Step>
    <Step active={progress.topics && !progress.flashcards}>
      Extracting key topics
    </Step>
    <Step pending={!progress.flashcards}>
      Generating practice flashcards
    </Step>
    <Step pending={!progress.studyPlan}>
      Building your study plan
    </Step>
  </AnimatedCheckmarks>
  
  <EstimatedTime>
    {estimatedSecondsRemaining}s remaining
  </EstimatedTime>
</OnboardingProgressScreen>
```

---

## Migration Strategy

### Phase 1: Backend (Week 1)

1. Add new fields to `Learning_Profile`:
   - `onboarding_state` (enum, default: `not_started`)
   - `exam_name`, `exam_date`, `skill_name`, `current_level`

2. Create new enums:
   - `OnboardingState`
   - Add `TEACHING`, `COMMUNITY` to `LearningPurpose`

3. Implement new endpoints:
   - `POST /learning/onboarding/exam-details`
   - `POST /learning/onboarding/skill-details`
   - `GET /learning/onboarding/status`

4. Refactor content generation:
   - Move from inline to background job
   - Implement progress tracking
   - Add status polling

5. Update existing endpoint:
   - `/onboarding/purpose` - set `onboarding_state`

### Phase 2: Web (Week 2)

1. No changes needed - web already uses form-based flow
2. Update to call new endpoints:
   - Use `/exam-details` or `/skill-details` instead of `/subjects`
   - Poll `/status` instead of `/home`
3. Minor UI tweaks for consistency

### Phase 3: Mobile (Week 2-3)

1. Create new screens:
   - `OnboardingPurposeScreen` (replace conversational chat)
   - `OnboardingExamDetailsScreen`
   - `OnboardingSkillDetailsScreen`
   - `OnboardingProgressScreen`

2. Deprecate:
   - Old `OnboardingScreen` (conversational)
   - Onboarding chat session logic
   - WebSocket onboarding events

3. Keep conversational as "Advanced Setup" option (optional future)

### Phase 4: Testing (Week 3)

1. Test all paths:
   - Email signup → exam prep
   - Google OAuth → skill building
   - Teaching path → space creation
   - Join space path → invite acceptance

2. Test interruption recovery:
   - Close app after purpose set
   - Reload → should resume at details form

3. Test cross-device:
   - Start on web, finish on mobile

---

## Success Metrics

### User Experience
- **Time to first content**: < 60 seconds from signup
- **Completion rate**: > 85% of activated users complete onboarding
- **Drop-off point**: Track where users abandon (purpose, details, progress)

### Technical
- **Content generation time**: < 30 seconds (p95)
- **API errors during onboarding**: < 0.1%
- **State consistency**: 100% (no users stuck in intermediate state)

### Consistency
- **Cross-platform parity**: Web and mobile flows match 100%
- **User confusion reports**: < 5% mention platform differences

---

## Open Questions

1. **Conversational onboarding future**:
   - Should we keep it as "Advanced Setup" option?
   - Or fully deprecate in favor of forms?
   - Decision: Deprecate initially, gather feedback, potentially bring back as opt-in

2. **Space creation in onboarding**:
   - Should teaching/community paths stay in onboarding flow?
   - Or treat as separate "Create Space" feature post-onboarding?
   - Decision: Keep in onboarding for now (existing behavior)

3. **Content generation timeout**:
   - What if AI topic extraction takes > 60 seconds?
   - Show message: "Taking longer than usual..." with option to skip?
   - Decision: Set 90s timeout, then let user proceed with basic preparation

4. **Resume onboarding UI**:
   - If user abandons at details step, how do we prompt them?
   - Banner on home: "Complete your setup"?
   - Decision: Modal on app open if `onboarding_state != 'completed'`

---

## Next Steps

1. ✅ Document current flows
2. ✅ Design unified specification
3. ⏭️ Implement backend changes (database, endpoints, background jobs)
4. ⏭️ Update mobile UI to match web flow
5. ⏭️ End-to-end testing and validation
