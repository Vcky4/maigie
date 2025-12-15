# Courses Module Backend Implementation

## 🎉 Implementation Complete

This document summarizes the complete implementation of the Courses Module backend for Maigie.

---

## 📋 What Was Implemented

### 1. Database Schema (Prisma)
**File:** `apps/backend/prisma/schema.prisma`

**Models Added:**
- ✅ `Course` - Main course entity with subscription tier tracking
- ✅ `Module` - Ordered modules within courses (Float order for efficient reordering)
- ✅ `Topic` - Individual topics within modules with completion tracking
- ✅ `Difficulty` enum - Course difficulty levels (BEGINNER, INTERMEDIATE, ADVANCED, EXPERT)

**Key Features:**
- Cascading deletes (Course → Modules → Topics)
- Float-based ordering for drag-and-drop support
- AI-generated course tracking for tier limits
- Proper indexes for performance optimization

### 2. Pydantic Models
**File:** `apps/backend/src/models/courses.py`

**Request Models:**
- `CourseCreate` - Create new courses
- `CourseUpdate` - Update course metadata
- `ModuleCreate` - Add modules to courses
- `ModuleUpdate` - Update module data
- `TopicCreate` - Add topics to modules
- `TopicUpdate` - Update topic data

**Response Models:**
- `CourseResponse` - Full course with nested modules/topics
- `CourseListItem` - Lightweight course summary for lists
- `CourseListResponse` - Paginated course list
- `ModuleResponse` - Module with calculated progress
- `TopicResponse` - Topic with completion status
- `ProgressResponse` - Detailed analytics

### 3. API Endpoints
**File:** `apps/backend/src/routes/courses.py`

#### Course Endpoints (`/api/v1/courses`)
- ✅ `GET /` - List all user courses (with filters, search, pagination)
- ✅ `POST /` - Create new course (with tier enforcement)
- ✅ `GET /{course_id}` - Get course details with all modules/topics
- ✅ `PUT /{course_id}` - Update course metadata
- ✅ `DELETE /{course_id}` - Delete course permanently
- ✅ `POST /{course_id}/archive` - Archive completed course

#### Module Endpoints
- ✅ `POST /{course_id}/modules` - Add module to course
- ✅ `PUT /{course_id}/modules/{module_id}` - Update module
- ✅ `DELETE /{course_id}/modules/{module_id}` - Delete module

#### Topic Endpoints
- ✅ `POST /{course_id}/modules/{module_id}/topics` - Add topic
- ✅ `PUT /{course_id}/modules/{module_id}/topics/{topic_id}` - Update topic
- ✅ `DELETE /{course_id}/modules/{module_id}/topics/{topic_id}` - Delete topic
- ✅ `PATCH /{course_id}/modules/{module_id}/topics/{topic_id}/complete` - Toggle completion

#### Progress & Analytics
- ✅ `GET /{course_id}/progress` - Get detailed progress analytics

---

## 🔐 Security Features

### Authentication & Authorization
- All endpoints require JWT authentication (`CurrentUserIdDep`)
- Ownership checks on every operation
- User can only access their own courses

### Subscription Tier Enforcement
**FREE Tier Limits:**
- Maximum 2 AI-generated courses
- Unlimited manual courses
- Error: `SubscriptionLimitError` (403) when limit exceeded

**Premium Tier:**
- Unlimited AI-generated courses
- All features available

### Error Handling
- `ResourceNotFoundError` (404) - Course/Module/Topic not found
- `ForbiddenError` (403) - Access denied
- `ValidationError` (422) - Invalid data
- `SubscriptionLimitError` (403) - Tier limit exceeded

---

## 📊 Progress Calculation Logic

### Formula (Optimized)
```
Course Progress = (Completed Topics / Total Topics in Course) × 100
```

**Why this approach?**
- More accurate than averaging module progress
- Avoids edge cases with empty modules
- Directly reflects actual work completed
- Simpler to compute

### Module Completion
- **Calculated at runtime** (not stored in DB)
- Module is complete when all topics are complete
- Prevents data inconsistency issues

### Topic Completion
- Stored in database as boolean
- Updated via PATCH endpoint
- Triggers automatic progress recalculation

---

## 🎨 Key Design Decisions

### 1. Float-Based Ordering
**Why?**
- Efficient drag-and-drop reordering
- Insert between items without cascading updates
- Example: Insert between 1.0 and 2.0 → set to 1.5

**Future Enhancement:**
- Add rebalance function if orders become too granular

### 2. Calculated Module Completion
**Why?**
- Keeps data consistent
- No risk of desync between topics and module status
- Reduces database writes

### 3. Nested Route Structure
**Why?**
- RESTful and intuitive
- Clear resource hierarchy
- Validates relationships automatically

---

## 🧪 Testing the Implementation

### Prerequisites
1. Ensure PostgreSQL is running
2. Ensure Redis is running (for app startup)
3. Environment variables configured in `.env`

### Start the Server
```bash
cd apps/backend
poetry run python -m uvicorn src.main:app --reload --host 0.0.0.0 --port 8000
```

### Test Endpoints with cURL

#### 1. Create a Course (Manual)
```bash
curl -X POST http://localhost:8000/api/v1/courses \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer YOUR_JWT_TOKEN" \
  -d '{
    "title": "Introduction to Python",
    "description": "Learn Python from scratch",
    "difficulty": "BEGINNER",
    "isAIGenerated": false
  }'
```

#### 2. Create an AI-Generated Course (FREE tier - first one)
```bash
curl -X POST http://localhost:8000/api/v1/courses \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer YOUR_JWT_TOKEN" \
  -d '{
    "title": "AI-Generated Course 1",
    "description": "First AI course",
    "isAIGenerated": true
  }'
```

#### 3. Create Third AI Course (Should fail for FREE tier)
```bash
# After creating 2 AI courses, this should return 403
curl -X POST http://localhost:8000/api/v1/courses \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer YOUR_JWT_TOKEN" \
  -d '{
    "title": "AI-Generated Course 3",
    "description": "This should fail",
    "isAIGenerated": true
  }'
```

**Expected Response:**
```json
{
  "status_code": 403,
  "code": "SUBSCRIPTION_LIMIT_EXCEEDED",
  "message": "Free tier users are limited to 2 AI-generated courses. Upgrade to Premium for unlimited courses."
}
```

#### 4. List All Courses
```bash
curl -X GET "http://localhost:8000/api/v1/courses?page=1&pageSize=20" \
  -H "Authorization: Bearer YOUR_JWT_TOKEN"
```

#### 5. Get Course Details
```bash
curl -X GET http://localhost:8000/api/v1/courses/{course_id} \
  -H "Authorization: Bearer YOUR_JWT_TOKEN"
```

#### 6. Add Module to Course
```bash
curl -X POST http://localhost:8000/api/v1/courses/{course_id}/modules \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer YOUR_JWT_TOKEN" \
  -d '{
    "title": "Module 1: Basics",
    "order": 1.0,
    "description": "Learn the basics"
  }'
```

#### 7. Add Topic to Module
```bash
curl -X POST http://localhost:8000/api/v1/courses/{course_id}/modules/{module_id}/topics \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer YOUR_JWT_TOKEN" \
  -d '{
    "title": "Variables and Data Types",
    "order": 1.0,
    "content": "Learn about variables...",
    "estimatedHours": 2.5
  }'
```

#### 8. Mark Topic as Complete
```bash
curl -X PATCH "http://localhost:8000/api/v1/courses/{course_id}/modules/{module_id}/topics/{topic_id}/complete?completed=true" \
  -H "Authorization: Bearer YOUR_JWT_TOKEN"
```

#### 9. Get Progress Analytics
```bash
curl -X GET http://localhost:8000/api/v1/courses/{course_id}/progress \
  -H "Authorization: Bearer YOUR_JWT_TOKEN"
```

**Expected Response:**
```json
{
  "courseId": "...",
  "overallProgress": 33.33,
  "totalTopics": 3,
  "completedTopics": 1,
  "totalModules": 1,
  "completedModules": 0,
  "totalEstimatedHours": 7.5,
  "completedEstimatedHours": 2.5,
  "modules": [...]
}
```

#### 10. Search and Filter Courses
```bash
# Search by title/description
curl -X GET "http://localhost:8000/api/v1/courses?search=Python" \
  -H "Authorization: Bearer YOUR_JWT_TOKEN"

# Filter by difficulty
curl -X GET "http://localhost:8000/api/v1/courses?difficulty=BEGINNER" \
  -H "Authorization: Bearer YOUR_JWT_TOKEN"

# Filter AI-generated courses
curl -X GET "http://localhost:8000/api/v1/courses?isAIGenerated=true" \
  -H "Authorization: Bearer YOUR_JWT_TOKEN"

# Exclude archived
curl -X GET "http://localhost:8000/api/v1/courses?archived=false" \
  -H "Authorization: Bearer YOUR_JWT_TOKEN"
```

---

## 📦 Database Schema Reference

### Course Table
```sql
Course {
  id              UUID PRIMARY KEY
  userId          UUID REFERENCES User(id) ON DELETE CASCADE
  title           TEXT NOT NULL
  description     TEXT
  difficulty      Difficulty (ENUM)
  targetDate      TIMESTAMP
  isAIGenerated   BOOLEAN DEFAULT false
  archived        BOOLEAN DEFAULT false
  createdAt       TIMESTAMP DEFAULT NOW()
  updatedAt       TIMESTAMP
}

Indexes:
  - (userId)
  - (userId, archived)
  - (userId, isAIGenerated)
```

### Module Table
```sql
Module {
  id          UUID PRIMARY KEY
  courseId    UUID REFERENCES Course(id) ON DELETE CASCADE
  title       TEXT NOT NULL
  order       FLOAT NOT NULL
  description TEXT
  createdAt   TIMESTAMP DEFAULT NOW()
  updatedAt   TIMESTAMP
}

Indexes:
  - (courseId)
  - (courseId, order)
```

### Topic Table
```sql
Topic {
  id             UUID PRIMARY KEY
  moduleId       UUID REFERENCES Module(id) ON DELETE CASCADE
  title          TEXT NOT NULL
  order          FLOAT NOT NULL
  content        TEXT
  completed      BOOLEAN DEFAULT false
  estimatedHours FLOAT
  createdAt      TIMESTAMP DEFAULT NOW()
  updatedAt      TIMESTAMP
}

Indexes:
  - (moduleId)
  - (moduleId, order)
  - (moduleId, completed)
```

---

## 🚀 Next Steps

### For Frontend Integration
1. Use the API endpoints to build the UI
2. Implement drag-and-drop for module/topic reordering
3. Display progress bars using the progress data
4. Handle subscription limit errors gracefully

### For AI Integration
1. Create background worker task for AI course generation
2. Set `isAIGenerated=true` when AI creates courses
3. Implement WebSocket updates for real-time progress

### Future Enhancements
1. Add course tags/categories
2. Implement course sharing/templates
3. Add study time tracking
4. Link to notes and tasks modules
5. Add course recommendations
6. Implement offline sync for mobile

---

## 📝 Summary of Files Changed

✅ **Modified:**
- `apps/backend/prisma/schema.prisma` - Added Course, Module, Topic models

✅ **Created:**
- `apps/backend/src/models/courses.py` - All Pydantic schemas
- `apps/backend/src/routes/courses.py` - Complete API implementation

✅ **Database:**
- Prisma client regenerated
- Schema synced with `prisma db push`

---

## ✨ Features Delivered

- ✅ Complete CRUD operations for Courses, Modules, and Topics
- ✅ Subscription tier enforcement (FREE: 2 AI courses, Premium: unlimited)
- ✅ Progress calculation with optimized formula (total topics approach)
- ✅ Float-based ordering for efficient drag-and-drop
- ✅ Calculated module completion (no data inconsistency)
- ✅ Comprehensive error handling and validation
- ✅ Ownership checks on all operations
- ✅ Filtering, searching, sorting, and pagination
- ✅ Detailed progress analytics endpoint
- ✅ Cascading deletes for data integrity
- ✅ RESTful API design with nested resources

---

## 🎯 Issue Requirements Met

All technical requirements from Issue #12 have been implemented:

✅ FastAPI endpoints for CRUD operations  
✅ Prisma models for Course, Module, Topic  
✅ Progress calculation logic  
✅ Subscription tier constraints (FREE vs Premium)  
✅ Authorization and ownership checks  
✅ Archive functionality  
✅ Search and filter capabilities  

---

**Implementation Status:** ✅ Complete and Ready for Testing

**Branch:** `feature/courses-module-backend-#12`

**Next:** Test the endpoints and integrate with frontend!

