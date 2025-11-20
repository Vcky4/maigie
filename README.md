# **Maigie – AI-Powered Study Companion**

### *Your personal AI-driven learning assistant*

Maigie is an AI-first study companion that helps students organize learning, generate courses, plan schedules, set goals, track progress, and access personalized resources—all through natural conversations (text + voice).

This repository is a **monorepo** managed using **Nx**, containing:

* **FastAPI backend**
* **Web App (Vite + React + shadcn-ui)**
* **Mobile App (Expo)**
* **AI Services (LLM, embeddings, voice)**

---

# 🚀 **Features**

### **AI + Productivity**

* Conversational AI (text + voice)
* AI-generated:

  * Courses (modules, topics, syllabus)
  * Goals (milestones, deadlines)
  * Schedules (daily/weekly plans)
  * Resource recommendations
  * Notes & summaries
* Realtime interactions via WebSockets

### **Dashboard Modules**

* Courses
* Goals
* Schedule (calendar + time blocks)
* Resource Library
* Notes
* Reminders
* Forecast (progress prediction)

### **Mobile + Web**

* Web: Vite + shadcn-ui
* Mobile: Expo
* Offline-first notes + tasks on mobile

### **Subscriptions**

* Free Tier (limited)
* Premium Monthly / Yearly
* Stripe-powered billing
* Auto-renewal & downgrade handling

---

# 📦 **Monorepo Structure**

```
maigie/
│
├── apps/
│   ├── web/                # Vite + React + shadcn-ui
│   ├── mobile/             # Expo React Native app
│   └── api/                # FastAPI backend
│
├── libs/
│   ├── ui/                 # shared UI components
│   ├── types/              # shared TypeScript interfaces
│   ├── utils/              # shared utilities
│   └── ai/                 # shared AI logic (client + contracts)
│
├── infra/
│   ├── docker/             # Docker configs
│   ├── scripts/            # infra scripts
│   └── db/                 # migrations / schema docs
│
├── tools/                  # Nx custom generators/executors
└── README.md
```

---

# ⚙️ **Tech Stack**

### **Frontend**

* Vite
* React
* shadcn-ui
* Zustand / Query
* TailwindCSS

### **Mobile**

* Expo (React Native)
* SQLite / WatermelonDB (offline sync)

### **Backend**

* FastAPI
* Prisma ORM
* PostgreSQL
* Redis (cache + events)
* WebSockets
* Background workers (RQ / Celery / custom)

### **AI**

* OpenAI / Anthropic models
* Embeddings search (Supabase, Pinecone, or pgvector)
* Whisper (voice → text)
* TTS (text → voice)

---

# 🧠 **AI Intent Architecture**

Maigie interprets user messages and maps them to structured actions:

* `create_course`
* `create_goal`
* `create_schedule`
* `recommend_resources`
* `summarize_notes`
* `progress_check`
* `reminder_set`
* and more…

Each AI response includes a JSON `action` block that the client uses to update the UI or trigger backend workflows.

Full spec is in `docs/llm/intent-spec.md`.

---

# 📱 **User Flows**

### Key flows include:

* Onboarding & preferences
* AI chat (text + voice)
* Auto-generated course → dashboard update
* Goal-setting via conversation
* Daily/weekly schedule flow
* Subscription upgrade & payment
* Mobile offline sync

Full detailed user flows are documented in:
`/docs/requirements/maigie_prd.md`

---

# 🛠️ **Development Setup**

### **1. Install dependencies**

```sh
pnpm install
```

### **2. Generate environment files**

Create `.env` files in:

```
apps/api/.env
apps/web/.env
apps/mobile/.env
```

Use templates from `env.example`.

### **3. Start backend**

```sh
nx serve api
```

### **4. Start web app**

```sh
nx serve web
```

### **5. Start mobile app**

```sh
nx run mobile:start
```

---

# 🗄️ **Database (Prisma + PostgreSQL)**

Key models:

* User
* Course
* Module
* Topic
* Goal
* ScheduleBlock
* Resource
* Note
* Reminder
* Subscription
* AIActionLog

Full schema in:
`apps/api/prisma/schema.prisma`

---

# 🔔 **Events System**

Backend uses Redis to emit events:

* `course.created`
* `goal.created`
* `schedule.updated`
* `subscription.changed`
* `reminder.trigger`

Clients listen via WebSockets for realtime UI updates.

---

# 🧪 **Testing**

```sh
nx run api:test
nx run web:test
nx run mobile:test
```

---

# 📦 **Deployment**

### Backend:

* Docker + Fly.io / Railway / Render
* Auto migrations
* Background workers for reminders + embeddings

### Web:

* Vercel / Netlify

### Mobile:

* Expo EAS Build & Deploy

---

# 📚 **Documentation**

* PRD: `/docs/requirements/maigie_prd.md`
* System architecture: `/docs/architecture/`
* AI intent routing: `/docs/ai/`
* API spec: `/docs/api/`

---

# 🧩 **Roadmap**

* Collaborative study sessions
* Shared notebooks
* AI flashcards
* Chrome extension
* Space repetition engine
* Study streak gamification

---

# ❤️ **Contributions**

Maigie is designed to eventually support open-source contributions.

Follow the contribution guide:
`/CONTRIBUTING.md`
