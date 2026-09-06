# Authentication and Onboarding Flows

**Status**: Documentation of current implementation (as of August 2026)  
**Purpose**: Map existing auth and onboarding patterns across backend, web, and mobile to identify gaps and design unified flow

---

## Current State Analysis

### Backend (FastAPI)

#### Auth Endpoints (`/api/v1/auth`)

**Signup Flow**:
1. `POST /auth/signup` - Creates user with `is_active=False`, sends OTP email
   - Input: `email`, `password`, `name`, optional `referralCode`
   - Returns: `UserResponse` (inactive until verified)
   - Generates 6-digit OTP, stores in `verification_code` with 15min expiry

2. `POST /auth/verify-email` - Activates account with OTP
   - Input: `email`, `code`
   - Sets `is_active=True`, clears verification code
   - Sends welcome email

3. `POST /auth/resend-otp` - Rate-limited to 1/minute
   - Generates new OTP with 15min expiry

**Login Flow**:
1. `POST /auth/login/json` - Email/password authentication
   - Returns: `access_token` (15min), `refresh_token`
   - Fails with `EMAIL_VERIFICATION_REQUIRED` if `is_active=False`

2. `POST /auth/refresh` - Exchange refresh token for new pair

**OAuth Flow**:
1. `GET /auth/oauth/{provider}/authorize` - Returns Google OAuth URL
   - Generates state token for CSRF protection
   - Optional `redirect_uri`, `referral_code`

2. `GET /auth/oauth/{provider}/callback` - Exchange code for tokens
   - Validates state, exchanges with Google
   - Calls `get_or_create_oauth_user()`:
     - If `provider_id` exists → return user
     - If email exists → link account, activate if needed
     - Else → create new user (already active, no OTP)

3. `POST /auth/oauth/native-callback` - Mobile-specific endpoint
   - Input: `id_token` from Google SDK
   - Validates token, same user creation logic
   - Returns: `access_token`, `refresh_token`

**Password Reset**:
- `POST /auth/forgot-password` - Send OTP to email
- `POST /auth/verify-reset-code` - Validate OTP before showing form
- `POST /auth/reset-password` - Complete reset with OTP + new password

#### Onboarding Endpoints (`/api/v1/learning`)

**Purpose**: These endpoints build the `Learning_Profile` and initial content

1. `POST /learning/onboarding/purpose` - Set purpose (exam_prep, skill_building, etc.)
   - Creates `Learning_Profile` if missing
   - Updates `purpose` field

2. `POST /learning/onboarding/subjects` - Set subjects and optional goals
   - Updates `subjects` and `goalsText`
   - **Triggers auto-setup**: creates preparation, extracts topics, generates flashcards, builds study plan
   - Returns immediately, content ready on next poll

3. `POST /learning/onboarding/complete` - Finalize onboarding
   - Sets `User.is_onboarded = True`
   - Records `Learning_Profile.onboardingCompletedAt`

4. `GET /learning/profile` - Get current profile
   - Returns purpose, subjects, goals, maturity, etc.

**Note**: The `identity/onboarding.py` file is a **stub**. All onboarding state is in `Learning_Profile` table managed by personal_learning domain.

#### User Model Fields

```python
class User:
    is_active: bool          # False until email verified (email signups only)
    is_onboarded: bool       # False until onboarding/complete called
    verification_code: str   # OTP for signup
    password_reset_code: str # OTP for password reset
    provider: str            # "email" or "google"
    provider_id: str         # Google sub claim (OAuth only)
```

---

### Web (React/TypeScript)

#### Auth Pages

**SignupPage** (`/signup`):
- Form: name, email, password
- Validation: email format, password ≥8 chars
- On submit → calls `POST /auth/signup`
- Stores email in localStorage, redirects to `/verify-otp`
- Also offers Google OAuth button

**VerifyOtpPage** (`/verify-otp`):
- 6-digit code input
- On verify → calls `POST /auth/verify-email`
- If backend returns tokens → auto-login
- Otherwise → redirects to `/login`

**LoginPage** (`/login`):
- Form: email, password
- On submit → calls `POST /auth/login/json`
- If `EMAIL_VERIFICATION_REQUIRED` → redirect to `/verify-otp`
- If `!user.isOnboarded` → redirect to `/onboarding`
- Else → redirect to `/` (home)
- Also offers Google OAuth button

**Google OAuth Flow**:
1. Click button → calls `/auth/oauth/google/authorize`
2. Redirects to Google consent screen
3. Google redirects to `/oauth/callback?code=...&state=...`
4. Callback page calls `/auth/oauth/google/callback`
5. Receives tokens, logs in, checks `isOnboarded` → routes accordingly

#### Onboarding Page

**OnboardingPage** (`/onboarding`):
- **Multi-step flow** with visual progress (0/3 steps)

**Step 1: Identity Selection**
- Shows 5 options:
  1. **Prepare for exams** → purpose: `EXAM_PREP`
  2. **Learn a skill** → purpose: `SKILL_BUILDING`
  3. **Teach or mentor** → goes to space creation
  4. **Create a community** → goes to space creation
  5. **Join a Learning Space** → shows pending invites

- On select → calls `POST /learning/onboarding/purpose` (for learner paths)
- Each option has accent color, icon, illustration

**Step 2: Subjects & Goals**
- Input: subject tags (add/remove)
- Textarea: optional goals description
- On submit → calls `POST /learning/onboarding/subjects`
- Can skip subjects (just completes onboarding)

**Step 3: Setting Up** (auto-progress)
- Shows animated progress: "Creating your learning foundation", "Organizing your key topics"
- Polls `GET /learning/home` every 2s until `stage !== 'setting_up'`
- On ready → calls `POST /learning/onboarding/complete`
- Redirects to `/` (home)

**Space Creation Flow** (teach/community paths):
- Form: space name, description, visibility
- On submit → creates space, completes onboarding

**Space Invitation Flow**:
- Loads pending invites from `GET /spaces/invites/pending`
- On accept → joins space, sets purpose to `GENERAL_LEARNING`, completes onboarding

#### Auth State Management

**AuthStore** (Zustand):
```typescript
{
  user: UserResponse | null
  accessToken: string | null
  refreshToken: string | null
  isAuthenticated: bool
  login(tokenResponse, user)
  logout()
  setUser(user)
  updateTokens(accessToken, refreshToken)
}
```

- Persisted to localStorage as `auth-storage`
- Tokens also in `localStorage.access_token`, `localStorage.refresh_token`
- API client auto-refreshes on 401 using refresh token

---

### Mobile (React Native/Expo)

#### Auth Screen

**AuthScreen** (`/auth`):
- **Toggle**: login ↔ signup (single screen)
- Signup form: name, email, password
- Login form: email, password
- Both offer Google OAuth button
- On signup → shows success toast, then must login
- On login → if `EMAIL_VERIFICATION_REQUIRED` → navigates to `/verify-otp`
- On success → if `!user.isOnboarded` → navigates to `/onboarding`

**VerifyOtpScreen** (`/verify-otp`):
- 6-digit code input with resend button
- On verify → calls `POST /auth/verify-email`
- If returns tokens → auto-login
- Navigates to `/onboarding` or `/today` based on `isOnboarded`

**Google OAuth**:
- Uses `@react-native-google-signin/google-signin` SDK
- On button press → triggers native Google sign-in
- Receives `idToken`
- Calls `POST /auth/oauth/native-callback` with `id_token`
- Backend validates, returns tokens
- Same routing logic: checks `isOnboarded`

#### Onboarding Screen

**OnboardingScreen** (`/onboarding`):
- **Completely different from web**: conversational AI chat flow
- Uses WebSocket connection to backend chat service
- Loads or creates "onboarding session" (dedicated chat group)
- Backend-seeded welcome message: "Hi! I'm your learning companion..."
- User types freeform messages or selects quick replies
- AI asks about goals, subjects, exam dates, etc.
- Can attach images (syllabus, textbooks)
- Backend auto-creates:
  - Learning profile (purpose, subjects)
  - Preparation (if exam prep intent detected)
  - Topics
  - Flashcards
  - Study plan
- Emits `onboarding_complete` event with `firstTopic` when done
- Redirects to `/studio/workspace/{courseId}/{moduleId}/{topicId}` or `/today`

**Progress Detection**:
- Polls `fetchUser()` every 800ms during chat
- When `user.isOnboarded` transitions to `true` → redirect

#### Auth State Management

**AuthContext** (React Context):
```typescript
{
  userToken: string | null
  user: UserResponse | null
  isLoading: bool
  login(email, password)
  signup(email, password, name)
  logout()
  googleLogin()
  verifyOtp(email, otp)
  resendOtp(email)
  fetchUser()
  ...password reset methods
}
```

- Tokens stored in AsyncStorage via ApiContext
- User state fetched on mount
- Auto-logout on 401 (session expired)

---

## Key Differences Between Web and Mobile

| Aspect | Web | Mobile |
|--------|-----|--------|
| **Auth UI** | Separate login/signup pages | Combined toggle screen |
| **Onboarding Type** | Purpose-first form (3 steps) | Conversational AI chat |
| **Subjects Input** | Tag input + textarea | Freeform chat messages |
| **Auto-Setup** | Polls `/learning/home` to check stage | Chat emits `onboarding_complete` event |
| **OAuth** | Browser redirect flow | Native SDK + `id_token` |
| **Session Store** | Zustand + localStorage | Context + AsyncStorage |

---

## Gaps and Issues

### Backend

1. **identity/onboarding.py is stub** - Onboarding state lives in personal_learning domain, not identity
   - No centralized "onboarding state machine"
   - `is_onboarded` flag is simplistic (just boolean)

2. **Auto-setup is synchronous** - `set_subjects()` calls `auto_setup_service.auto_setup_for_learner()` inline
   - Blocks for AI topic extraction, flashcard generation
   - Web works around this with polling, mobile with events

3. **No onboarding progress tracking** - Can't resume interrupted onboarding
   - If user closes page after subjects, no way to know where they were

4. **OAuth account linking** - If email exists, OAuth activation logic is implicit
   - Not documented, edge cases untested

### Web

5. **Inconsistent token storage** - Tokens in both Zustand store AND localStorage
   - Can get out of sync
   - Unclear source of truth

6. **Space creation in onboarding** - Teach/community paths create spaces but don't track properly
   - Should these even be in onboarding? (Different domain concern)

### Mobile

7. **Conversational onboarding is completely separate** - No code reuse with web
   - Different UX philosophy (chat vs forms)
   - Harder to maintain consistency

8. **Chat-based onboarding requires WebSocket** - More complex infra
   - What if WebSocket fails during onboarding?
   - No fallback shown

9. **No way to skip conversational flow** - Must chat to completion
   - Power users might want quick form-based setup

### Cross-Platform

10. **Different flows confuse users** - If user starts on web, continues on mobile (or vice versa)
    - Web: "I set my purpose to exam prep"
    - Mobile: "Hi! Tell me about your goals..." (starts over)

11. **Google OAuth requires different client IDs** - Web client ID vs iOS/Android client IDs
    - Configuration complexity
    - Must maintain 3 separate OAuth apps

---

## Recommendations for Unified Flow

### Short Term (Fix Critical Issues)

1. **Align mobile onboarding with web** - Replace conversational chat with purpose-first form
   - Keep chat available as "advanced setup" option
   - Default to form (faster, more predictable)

2. **Make auto-setup async** - Move to background job with status polling
   - `POST /learning/onboarding/subjects` returns immediately
   - Client polls `GET /learning/onboarding/status` endpoint
   - Both web and mobile use same pattern

3. **Centralize token management** - Pick one source of truth
   - Web: localStorage only (remove Zustand token fields)
   - Mobile: AsyncStorage only (via ApiContext)

### Medium Term (Improve UX)

4. **Add onboarding state machine** - Track progress explicitly
   - States: `not_started`, `purpose_set`, `subjects_set`, `content_ready`, `completed`
   - Store in `Learning_Profile.onboarding_state` field
   - Enable "resume onboarding" if interrupted

5. **Unified OAuth configuration** - Document client ID setup clearly
   - Provide setup script/checklist
   - Validate config on app startup

6. **Progressive onboarding** - Don't block on auto-setup completion
   - Create preparation skeleton immediately
   - Backfill topics/flashcards/plan asynchronously
   - User can start using app while content generates

### Long Term (Architecture)

7. **Separate identity from learning concerns** - Move onboarding state to identity domain
   - `identity/onboarding_state` table with FSM
   - Personal learning creates profile when identity onboarding completes
   - Cleaner domain boundaries

8. **Offer onboarding mode choice** - Let users pick their style
   - "Quick setup" (form-based, 2 min)
   - "Guided conversation" (chat-based, 5-10 min)
   - Both create same backend state

9. **Cross-device continuity** - Store onboarding progress server-side
   - User can start on web, finish on mobile
   - Sync state via API

---

## Next Steps

1. ✅ Document current state (this file)
2. ⏭️ Design unified flow specification
3. ⏭️ Implement backend onboarding state machine
4. ⏭️ Update mobile to use form-based onboarding
5. ⏭️ Test end-to-end flows on both platforms
