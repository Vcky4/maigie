# **Maigie – Full Product Requirements Document (PRD)**

*A complete, end‑to‑end requirements document including user flows, system features, subscription logic, and frontend interactions (web + mobile).*

---

# **1. Product Overview**

Maigie is an AI‑powered academic operating system designed to help students organize learning, generate study plans, manage goals, track progress, and access personalized resources in one coordinated workflow. It includes conversational AI, voice interaction, scheduling, recommendations, reminders, and dashboards that automatically adapt to user behavior.

This PRD outlines:

* Functional requirements
* Non‑functional requirements
* User roles
* Subscription system requirements
* Web (Vite + shadcn-ui) flows
* Mobile (Expo) flows
* Backend + AI requirements
* System constraints
* Edge cases
* Acceptance criteria for each feature

---

# **2. User Types**

### **2.1. Guest User**

* Can view landing page
* Can explore the product demo
* Cannot use AI chat or create courses/schedules
* Must sign up or subscribe to continue

### **2.2. Basic User (Free Tier)**

* Limited AI chat (50 messages/month)
* No voice AI
* Max 2 AI-generated courses
* Max 2 active goals
* No advanced scheduling
* Resource recommendations are capped
* Ads present (optional depending on business model)

### **2.3. Premium User (Paid Subscription)**

* Unlimited AI chat
* Full voice conversation capabilities
* Unlimited AI-generated courses, goals, schedules
* Resource library access
* Multi-device sync
* Advanced insights + reports

---

# **3. Core Features (High-Level)**

### 1. AI Conversational Hub

### 2. Auto-created Courses, Goals & Schedules

### 3. Manual editing tools

### 4. Dashboard with tiles (Courses, Goals, Schedule, Resources, Notes, Reminders, Forecast)

### 5. Voice interface

### 6. Calendar + time blocking

### 7. Resource recommendation engine

### 8. Subscription & billing

### 9. Profile settings

### 10. Analytics + Progress visualization

---

# **4. Detailed Functional Requirements**

## **4.1. Authentication + Onboarding**

**Users can:**

* Sign up using email/password or OAuth (Google)
* Confirm email (optional depending on MVP)
* Set preferences such as:

  * Study interests
  * Academic level
  * Time availability
  * Timezone

## **4.2. AI Chat (Text + Voice)**

* Conversational interface
* AI can:

  * Create courses
  * Create study goals
  * Generate schedules
  * Recommend resources
  * Summarize notes
  * Explain difficult topics
* AI responses must follow JSON action schema when creating content

## **4.3. Dashboard**

### Sections:

1. **Courses** – list view + quick add
2. **Goals** – (AI + manual)
3. **Schedules** – calendar, timeline view
4. **Resources** – recommended sets
5. **Daily Schedule / Forecast** – auto-generated
6. **Reminders** – push notification integration

## **4.4. Courses Module**

* AI auto‑creates modules & topics
* User can edit:

  * Title
  * Description
  * Modules
  * Topics
* Progress tracking (completion %)
* Integration with notes + tasks

## **4.5. Goals Module**

* Goals created via AI or manually
* Goals include:

  * Title
  * Target date
  * Subtasks / tasks
  * Progress indicator

## **4.6. Scheduling Module**

* Daily/weekly timetable
* AI-generated or manual blocks
* Reminders via push notifications
* Recurring rules supported
* Conflict detection

## **4.7. Notes Module**

* Rich text
* AI summarization
* Auto linking to courses
* Voice-to-text support

## **4.8. Resource Recommendations**

* AI selects based on:

  * Course topic
  * Difficulty level
  * Past learning behavior

## **4.9. Subscription System**

### Plans:

* Free
* Premium Monthly
* Premium Yearly

### Features of subscription flow:

* Stripe/Braintree support
* Cancel anytime
* Auto-renewal
* Grace period handling
* Downgrade behavior (e.g. freeze courses beyond limits)

---

# **5. Detailed Frontend User Flows (Very Detailed)**

## **5.1. Landing Page (Web Only – Vite)**

1. User opens maigie.com
2. They see hero section:

   * Call-to-action buttons
   * Video/graphic demo
3. Buttons:

   * "Start for Free"
   * "Login"
4. Scrolling reveals:

   * Features
   * Pricing
   * Testimonials
5. User clicks **Start for Free → Signup**

---

# **5.2. Signup Flow (Web + Mobile)**

1. User enters email + password
2. Chooses preferences
3. Selects study interests
4. AI introduces itself (“What do you want to learn today?”)
5. User is placed inside the **AI chat hub**

---

# **5.3. AI Chat Flow**

### **Text Input Flow**

1. User types: *"I want to study Data Structures for my exam in 6 weeks"*
2. Backend:

   * Detects intent
   * LLM returns `create_course` and `create_goal`
3. UI displays loader animation
4. Course + goal appear as notifications:

   * "Course created"
   * "Goal added"
5. Dashboard auto-updates

### **Voice Input Flow**

1. User taps mic icon
2. Speaks request
3. Speech-to-text converts
4. Same AI logic as text

---

# **5.4. Course Flow**

### View Course

1. User taps a course tile
2. Course detail page opens:

   * Title
   * Modules
   * Progress bar

### Edit Course

1. User taps "Edit"
2. Edit modal opens (shadcn-UI form)
3. User updates modules
4. Press **Save**
5. Backend updates + emits event

---

# **5.5. Goal Flow**

1. User opens "Goals" tab
2. Sees list of goals
3. Clicking a goal opens detailed view
4. User marks tasks as done or edits the goal
5. AI may suggest improvements (e.g., "You're behind schedule")

---

# **5.6. Scheduling Flow**

### Daily View

1. User opens "Schedule"
2. Timeline view shows blocks (AI‑generated or manual)

### Add Schedule Manually

1. User taps "+ Block"
2. Form opens
3. User sets date/time
4. Saves block

### AI Auto Scheduling

1. AI analyzes goals + deadlines
2. Suggests blocks
3. User approves or dismisses

---

# **5.7. Subscription Flow (Web + Mobile)**

### Flow Steps:

1. User clicks **Upgrade to Premium**
2. Pricing modal appears
3. User selects Monthly or Yearly
4. Redirect to payment provider
5. Payment confirmation
6. App updates user role → Premium
7. Unlocks premium features

### Downgrade Flow:

1. User cancels subscription
2. System records end-of-cycle date
3. When cycle ends:

   * User is moved to Free tier
   * If they exceed limits:

     * Courses freeze
     * AI chat reduced

---

# **6. Backend Requirements**

### Includes:

* FastAPI
* Prisma ORM
* PostgreSQL
* Redis for caching + events
* WebSockets for real-time updates
* LLM-driven intent engine
* Voice pipeline (speech-to-text + text-to-speech)
* Background workers for reminders, scheduling, embedding indexing

---

# **7. Analytics Requirements**

* Track AI usage
* Track study time
* Track retention and progress
* Funnel tracking for subscription upsell

---

# **8. Non-Functional Requirements**

* Fast load times (<1.2s web target)
* Mobile fully offline-capable for notes & tasks
* Scalable (multi-region ready)
* Accessible (WCAG AA)

---

# **9. Acceptance Criteria**

Each feature will receive a list of testable conditions in the next iteration.

---

If you'd like, I can now add:

* UX wireframes
* Acceptance criteria per feature
* API endpoints mapping
* A proper Gantt timeline for development
* System diagram (architecture)
