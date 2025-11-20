# Data Models (Core Tables) — Simplified ERD

Entities (high-level): Users, Courses, Modules, Enrollments, Goals, Tasks, Notes, Resources, Schedules, Reminders, AIConversations, Embeddings

## Example Schemas (Postgres / Prisma-like)

```
User(id PK, email, name, password_hash, created_at, timezone, preferences JSON)
Course(id PK, ownerId FK->User, title, description, metadata JSON, created_at)
Module(id PK, courseId FK->Course, title, content, order)
Enrollment(id PK, userId FK->User, courseId FK->Course, role, enrolled_at)
Goal(id PK, userId, title, description, target_date, status, progress float)
Task(id PK, userId, goalId?, courseId?, title, notes, due_at, status)
Note(id PK, userId, content, title, linkedCourseId?, embeddings_id)
Resource(id PK, title, url, type(enum), metadata JSON)
ScheduleBlock(id PK, userId, title, start_ts, end_ts, recurring_rule)
AIConversation(id PK, userId, messages JSON, metadata)
Embedding(id PK, object_type, object_id, vector)
```

## Notes

* Store `preferences` and `metadata` as JSONB for flexibility.
* Use `pgvector` or an external vector DB (Qdrant/Pinecone). If using Postgres+pgvector, keep vectors in a separate table to avoid bloat.

---

# Detailed DB Schema (Prisma)

Below is a pragmatic Prisma schema for the core models. Adjust types to match your DB provider.

```prisma
generator client {
  provider = "prisma-client-js"
}

datasource db {
  provider = "postgresql"
  url      = env("DATABASE_URL")
}

model User {
  id            String    @id @default(cuid())
  email         String    @unique
  name          String?
  passwordHash  String
  timezone      String    @default("Africa/Lagos")
  preferences   Json?
  createdAt     DateTime  @default(now())
  courses       Course[]  @relation("owner_courses")
  enrollments   Enrollment[]
  goals         Goal[]
  notes         Note[]
  schedules     ScheduleBlock[]
}

model Course {
  id          String    @id @default(cuid())
  ownerId     String
  owner       User      @relation(fields: [ownerId], references: [id], name: "owner_courses")
  title       String
  description String?
  metadata    Json?
  modules     Module[]
  enrollments Enrollment[]
  createdAt   DateTime  @default(now())
}

model Module {
  id        String   @id @default(cuid())
  courseId  String
  course    Course   @relation(fields: [courseId], references: [id])
  title     String
  content   String?
  order     Int
}

model Enrollment {
  id        String  @id @default(cuid())
  userId    String
  courseId  String
  role      String  @default("student")
  enrolledAt DateTime @default(now())
  user      User    @relation(fields: [userId], references: [id])
  course    Course  @relation(fields: [courseId], references: [id])
}

model Goal {
  id          String   @id @default(cuid())
  userId      String
  title       String
  description String?
  targetDate  DateTime?
  status      String   @default("active")
  progress    Float    @default(0.0)
  createdAt   DateTime @default(now())
  user        User     @relation(fields: [userId], references: [id])
}

model Task {
  id        String   @id @default(cuid())
  userId    String
  goalId    String?
  courseId  String?
  title     String
  notes     String?
  dueAt     DateTime?
  status    String   @default("todo")
  createdAt DateTime @default(now())
  user      User     @relation(fields: [userId], references: [id])
}

model Note {
  id             String   @id @default(cuid())
  userId         String
  title          String?
  content        String
  linkedCourseId String?
  embeddingsId   String?
  createdAt      DateTime @default(now())
  user           User     @relation(fields: [userId], references: [id])
}

model Resource {
  id          String  @id @default(cuid())
  title       String
  url         String
  type        String
  metadata    Json?
  recommended Boolean @default(false)
  score       Float?
  createdAt   DateTime @default(now())
}

model ScheduleBlock {
  id           String   @id @default(cuid())
  userId       String
  title        String
  startAt      DateTime
  endAt        DateTime
  recurringRule String?
  createdAt    DateTime @default(now())
  user         User     @relation(fields: [userId], references: [id])
}

model AIConversation {
  id         String   @id @default(cuid())
  userId     String
  messages   Json
  metadata   Json?
  createdAt  DateTime @default(now())
  user       User     @relation(fields: [userId], references: [id])
}

model Embedding {
  id         String   @id @default(cuid())
  objectType String
  objectId   String
  vector     Bytes
  createdAt  DateTime @default(now())
}
```

## Notes

* `Embedding.vector` uses `Bytes` — when using Postgres+pgvector you may choose custom type mapping.
* Add indexes on frequently queried fields (e.g. `Resource(url)`, `Note.userId`, `ScheduleBlock.userId`, `Embedding(objectType, objectId)`).
* Add full-text search indices where appropriate.

