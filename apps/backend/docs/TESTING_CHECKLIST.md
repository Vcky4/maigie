# Unified Onboarding Testing Checklist

**Status**: Ready for testing (pending database access)  
**Date**: August 9, 2026

---

## Prerequisites

Before testing, ensure:

- [ ] Backend database is accessible
- [ ] Alembic migration 015 has been run successfully
- [ ] Backend server is running (`poetry run uvicorn src.main:app --reload`)
- [ ] Web frontend is running (`npm run dev` in maigie-client)
- [ ] Mobile app is built and running (Expo Go or built app)
- [ ] All three have correct API endpoints configured

---

## Backend API Testing

### 1. Database Migration

```bash
cd apps/backend
poetry run alembic upgrade head
```

**Verify**:
```sql
\d+ "LearningProfile"
```

Should show columns: `onboardingState`, `examName`, `examDate`, `skillName`, `currentLevel`

**Check backfill**:
```sql
SELECT "onboardingState", COUNT(*) 
FROM "LearningProfile" 
GROUP BY "onboardingState";
```

### 2. API Endpoint Tests

#### Setup: Get Auth Token

```bash
# Signup
curl -X POST http://localhost:8000/api/v1/auth/signup \
  -H "Content-Type: application/json" \
  -d '{
    "email": "test@example.com",
    "password": "password123",
    "name": "Test User"
  }'

# Verify email (get code from logs or email)
curl -X POST http://localhost:8000/api/v1/auth/verify-email \
  -H "Content-Type: application/json" \
  -d '{
    "email": "test@example.com",
    "code": "123456"
  }'

# Login
TOKEN=$(curl -X POST http://localhost:8000/api/v1/auth/login/json \
  -H "Content-Type: application/json" \
  -d '{
    "email": "test@example.com",
    "password": "password123"
  }' | jq -r '.access_token')

echo "Token: $TOKEN"
```

#### Test Exam Prep Flow

```bash
# 1. Set purpose
curl -X POST http://localhost:8000/api/v1/learning/onboarding/purpose \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"purpose": "exam_prep"}'

# Expected: 201, onboardingState = "purpose_set"

# 2. Set exam details
curl -X POST http://localhost:8000/api/v1/learning/onboarding/exam-details \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "examName": "SAT",
    "examDate": "2027-06-15",
    "subjects": ["Math", "Reading", "Writing"],
    "goals": "Score 1500+ for college"
  }'

# Expected: 200, onboardingState = "details_set"

# 3. Poll status (repeat every 2 seconds)
curl -X GET http://localhost:8000/api/v1/learning/onboarding/status \
  -H "Authorization: Bearer $TOKEN"

# Expected: Initially state = "details_set", eventually "content_ready"

# 4. Complete onboarding
curl -X POST http://localhost:8000/api/v1/learning/onboarding/complete \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{}'

# Expected: 204, user.is_onboarded = true

# 5. Verify user
curl -X GET http://localhost:8000/api/v1/auth/me \
  -H "Authorization: Bearer $TOKEN"

# Expected: isOnboarded = true
```

#### Test Skill Building Flow

```bash
# Use same TOKEN from above or create new user

# 1. Set purpose
curl -X POST http://localhost:8000/api/v1/learning/onboarding/purpose \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"purpose": "skill_building"}'

# 2. Set skill details
curl -X POST http://localhost:8000/api/v1/learning/onboarding/skill-details \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "skillName": "Python Programming",
    "currentLevel": "beginner",
    "subjects": ["Data Structures", "Web Dev"],
    "goals": "Build full-stack apps"
  }'

# 3. Poll status and complete (same as above)
```

#### Test Backward Compatibility

```bash
# Old endpoint should still work
curl -X POST http://localhost:8000/api/v1/learning/onboarding/subjects \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "subjects": ["Math", "Science"],
    "goals": "Learn faster"
  }'

# Expected: 200, triggers auto-setup
```

### 3. Database Verification

After completing flows, verify:

```sql
-- Check profile state
SELECT id, "userId", "onboardingState", purpose, "examName", "skillName"
FROM "LearningProfile"
WHERE "userId" = 'user_id_here';

-- Check user onboarded flag
SELECT id, email, "isOnboarded"
FROM "User"
WHERE email = 'test@example.com';

-- Check preparations created
SELECT id, "userId", subject, type, "targetDate"
FROM "ExamPrep"
WHERE "userId" = 'user_id_here';
```

---

## Web Frontend Testing

### 1. Signup Flow → Exam Prep

1. Navigate to `http://localhost:3000/signup`
2. Fill form: name, email, password
3. Click "Sign up"
4. **Verify**: Redirected to `/verify-otp`
5. Enter 6-digit code from email
6. Click "Verify"
7. **Verify**: Redirected to `/onboarding`

**Onboarding - Purpose**:
8. **Verify**: Shows "Welcome, [FirstName]. Where should we begin?"
9. **Verify**: 5 purpose cards displayed with icons
10. Click "Prepare for exams"
11. **Verify**: Loading state shown
12. **Verify**: Redirected to subjects/details step

**Onboarding - Details**:
13. **Verify**: Form shows: subject tags, goals textarea
14. Add subjects: "Math", "Reading", "Writing"
15. Enter goals: "Score 1500+ for college applications"
16. Click "Continue"
17. **Verify**: Redirected to progress screen

**Onboarding - Progress**:
18. **Verify**: Shows "Setting up" with animated steps
19. **Verify**: Steps progress: preparation → topics → flashcards → plan
20. **Verify**: Estimated time shown
21. **Wait** for completion (~30 seconds)
22. **Verify**: Automatically redirected to `/home` or `/prep/{id}`

**Final Verification**:
23. Navigate to `/`
24. **Verify**: User is logged in
25. **Verify**: Preparation visible in dashboard

### 2. Login Flow → Skill Building

1. Navigate to `http://localhost:3000/login`
2. Login with existing account that isn't onboarded
3. **Verify**: Redirected to `/onboarding`
4. Select "Learn a skill"
5. Enter details (similar to exam prep)
6. Complete flow
7. **Verify**: Redirected to home, content created

### 3. Google OAuth Flow

1. Navigate to `/signup`
2. Click "Sign up with Google"
3. Complete Google consent
4. **Verify**: Redirected back to app
5. **Verify**: If new user → `/onboarding`
6. **Verify**: If existing onboarded user → `/home`

### 4. Edge Cases

**Interrupted Onboarding**:
1. Start onboarding, select purpose
2. Close browser tab
3. Reopen, navigate to `/onboarding`
4. **Verify**: Resumes at details step (not purpose)

**Back Navigation**:
1. Start exam prep onboarding
2. Reach details screen
3. Click back button
4. **Verify**: Returns to purpose selection
5. Select different purpose
6. **Verify**: Routes to appropriate details screen

**Form Validation**:
1. Reach details screen
2. Leave required fields empty
3. Click "Continue"
4. **Verify**: Error message shown
5. **Verify**: Form not submitted

---

## Mobile Testing

### 1. Signup Flow → Exam Prep

1. Open app, navigate to Auth screen
2. Toggle to "Sign up"
3. Fill: name, email, password
4. Tap "Sign Up"
5. **Verify**: Navigated to Verify OTP screen
6. Enter 6-digit code
7. Tap "Verify"
8. **Verify**: Navigated to `/onboarding/purpose`

**Purpose Selection**:
9. **Verify**: Shows welcome with 5 cards
10. **Verify**: Progress dots: 1 active, 2 inactive
11. Tap "Prepare for exams"
12. **Verify**: Card shows selected state
13. **Verify**: Navigated to `/onboarding/exam-details`

**Exam Details**:
14. **Verify**: Progress dots: 1 complete, 1 active, 1 inactive
15. **Verify**: Back button visible
16. Enter exam name: "SAT"
17. Tap date button, select date
18. Add subjects using + button
19. Enter goals in textarea
20. Tap "Continue"
21. **Verify**: Navigated to `/onboarding/progress`

**Progress**:
22. **Verify**: Shows 4 steps with icons
23. **Verify**: Steps animate from empty → loading → complete
24. **Verify**: Estimated time displayed
25. **Wait** for completion
26. **Verify**: Auto-navigated to exam prep or Today tab

### 2. Google OAuth Flow

1. Open app, tap "Sign in with Google"
2. Complete native Google sign-in
3. **Verify**: If new → onboarding
4. **Verify**: If existing → Today tab

### 3. Platform-Specific Tests

**iOS**:
- [ ] Date picker shows iOS spinner style
- [ ] Keyboard dismisses on scroll
- [ ] Safe area insets respected
- [ ] Back swipe gesture works

**Android**:
- [ ] Date picker shows Android calendar
- [ ] Hardware back button works
- [ ] Keyboard behavior correct
- [ ] StatusBar color matches theme

### 4. Mobile Edge Cases

**Interrupted Flow**:
1. Start onboarding, select purpose
2. Force quit app (swipe away)
3. Reopen app
4. **Verify**: Resumes at appropriate step

**Network Error**:
1. Turn off WiFi/data
2. Try to submit form
3. **Verify**: Toast error shown
4. **Verify**: Form stays on screen (not navigated away)
5. Turn on network
6. Retry submit
7. **Verify**: Proceeds normally

**Rotation** (if supported):
1. Rotate device during onboarding
2. **Verify**: Layout adjusts correctly
3. **Verify**: Form data preserved

---

## Cross-Platform Testing

### 1. State Persistence

**Scenario**: Start on web, finish on mobile

1. Signup on web, select purpose "exam_prep"
2. Close browser (don't complete)
3. Login on mobile with same account
4. Navigate to onboarding
5. **Verify**: Shows exam details form (not purpose)
6. Complete onboarding on mobile
7. Return to web, refresh
8. **Verify**: User is onboarded, sees home

**Scenario**: Start on mobile, finish on web

1. Signup on mobile, select purpose "skill_building"
2. Close app (don't complete)
3. Login on web with same account
4. Navigate to onboarding
5. **Verify**: Shows skill details form
6. Complete onboarding on web
7. Return to mobile, pull to refresh
8. **Verify**: User is onboarded, sees home

### 2. Concurrent Editing

**Scenario**: Same user, two devices simultaneously

1. Login on web and mobile with same account
2. On web: Select purpose "exam_prep"
3. On mobile: Refresh, select purpose "skill_building"
4. **Verify**: Last write wins (likely mobile)
5. Complete flow on one device
6. Refresh other device
7. **Verify**: Both show onboarded state

---

## Error Handling

### 1. API Errors

**401 Unauthorized**:
- Scenario: Token expires during onboarding
- Expected: Logout, redirect to login

**500 Server Error**:
- Scenario: Backend crashes during content generation
- Expected: Toast/alert shown, user can retry

**Network Timeout**:
- Scenario: Poor connection, request takes > 30s
- Expected: Timeout error, user can retry

### 2. Validation Errors

**Missing Required Field**:
- Scenario: Submit exam details without exam name
- Expected: Field highlighted, error message shown

**Invalid Date**:
- Scenario: Select past date for exam
- Expected: Warning shown (or accepted if retake)

**Duplicate Subject**:
- Scenario: Add same subject twice
- Expected: Ignored silently or show "Already added"

### 3. State Errors

**Profile Doesn't Exist**:
- Scenario: User never created profile
- Expected: Auto-create on first onboarding call

**Already Onboarded**:
- Scenario: is_onboarded = true, try to access /onboarding
- Expected: Redirect to home

---

## Performance Testing

### 1. Content Generation Time

**Measure**: Time from "Continue" click to progress completion

- [ ] Record time for exam prep flow
- [ ] Record time for skill building flow
- [ ] Average should be < 30 seconds
- [ ] P95 should be < 60 seconds

### 2. Status Polling Overhead

**Measure**: Network requests during progress screen

- [ ] Count requests (should be ~15 for 30 second flow)
- [ ] Check response size (should be < 1KB)
- [ ] Verify no memory leaks from polling

### 3. Mobile Performance

- [ ] Measure app startup time
- [ ] Measure screen transition time
- [ ] Check memory usage during onboarding
- [ ] Verify no frame drops during animations

---

## Accessibility Testing

### Web

- [ ] Tab navigation works through all forms
- [ ] Screen reader announces all labels
- [ ] Error messages announced
- [ ] Buttons have accessible names
- [ ] Color contrast passes WCAG AA

### Mobile

- [ ] VoiceOver/TalkBack works on all screens
- [ ] Touch targets ≥ 44x44 points
- [ ] Form labels associated with inputs
- [ ] Loading states announced

---

## Security Testing

### 1. Auth Flow

- [ ] Tokens stored securely (httpOnly cookies on web, AsyncStorage/Keychain on mobile)
- [ ] Refresh token rotation works
- [ ] Expired tokens handled gracefully
- [ ] CSRF protection on state-changing endpoints

### 2. Data Validation

- [ ] SQL injection: Try `'; DROP TABLE User; --` in form fields
- [ ] XSS: Try `<script>alert('xss')</script>` in goals field
- [ ] File upload: Try uploading non-image files (if image upload added)

### 3. Rate Limiting

- [ ] Signup endpoint rate limited
- [ ] Status polling doesn't cause rate limit
- [ ] Password reset rate limited

---

## Regression Testing

Ensure existing features still work:

- [ ] Existing onboarded users can login normally
- [ ] Space creation still works (teaching/community paths)
- [ ] Pending invites flow still works
- [ ] Profile editing works post-onboarding
- [ ] Credits/billing flows unaffected
- [ ] Exam prep features work with new onboarding-created preps

---

## Deployment Checklist

Before deploying to production:

1. **Database**:
   - [ ] Run migration in staging first
   - [ ] Backup production DB
   - [ ] Run migration in production
   - [ ] Verify backfill succeeded

2. **Backend**:
   - [ ] Deploy new backend code
   - [ ] Verify health check passes
   - [ ] Check error rates in monitoring

3. **Web**:
   - [ ] Build web frontend
   - [ ] Deploy to CDN
   - [ ] Verify routing works

4. **Mobile**:
   - [ ] Build mobile apps (iOS + Android)
   - [ ] Submit to App Store / Play Store
   - [ ] Wait for approval
   - [ ] Release to users

5. **Monitoring**:
   - [ ] Set up alerts for onboarding completion rate
   - [ ] Set up alerts for API error rates
   - [ ] Track content generation time
   - [ ] Monitor background job queue

6. **Rollback Plan**:
   - [ ] Document rollback steps
   - [ ] Keep old mobile app version available
   - [ ] Can revert web deploy instantly
   - [ ] Can downgrade DB migration if needed

---

## Sign-Off

Once all tests pass:

- [ ] Backend developer sign-off
- [ ] Web frontend developer sign-off
- [ ] Mobile developer sign-off
- [ ] QA sign-off
- [ ] Product owner sign-off
- [ ] Deploy to production

---

## Post-Deployment Monitoring

First 24 hours after deploy:

- [ ] Monitor onboarding completion rate (should be > 80%)
- [ ] Check error rates (should be < 1%)
- [ ] Review user feedback
- [ ] Check content generation times
- [ ] Monitor database load

First week:

- [ ] Analyze drop-off points
- [ ] Collect user feedback
- [ ] Plan optimizations if needed
- [ ] Consider A/B testing variations
