# Courses Module

## Issue Type
Feature

## Priority
High

## Labels
- courses
- learning
- core-feature
- mvp

## Description

Implement the courses management system where users can view, create, edit, and track their learning courses. Courses are structured with modules and topics, can be AI-generated or manually created, and include progress tracking.

## User Stories

### As a user:
- I want to view all my courses in an organized list
- I want to see course details including modules and topics
- I want AI to automatically create course structures
- I want to manually edit course content
- I want to track my progress through each course
- I want to link notes and tasks to courses

## Functional Requirements

### Course Structure
- Course contains:
  - Title
  - Description
  - Modules (ordered list)
  - Topics (within modules)
  - Progress percentage
  - Created date
  - Target completion date (optional)
  - Tags/categories
  - Difficulty level

### Course Creation
- **AI-generated**: AI creates complete course structure via chat
- **Manual creation**: User creates course with form
- **Hybrid**: AI creates base, user edits

### Course Management
- View course list (card or table view)
- Search and filter courses
- Sort by progress, date, name
- Archive completed courses
- Delete courses (with confirmation)

### Course Detail View
- Display complete module structure
- Show progress indicators
- List associated notes and tasks
- Display recommended resources
- Show study time spent
- Edit course metadata

### Course Editing
- Edit course title and description
- Add/remove/reorder modules
- Add/remove/reorder topics
- Update difficulty level
- Modify completion dates
- Real-time save (auto-save or manual)

### Progress Tracking
- Track completion per topic
- Calculate module completion percentage
- Calculate overall course progress
- Visualize progress over time
- Set completion milestones

### Integration
- Link to related goals
- Connect to schedule blocks
- Associate notes with courses
- Link to recommended resources

## Technical Requirements

### Backend
- FastAPI endpoints for CRUD operations
- Prisma models for Course, Module, Topic
- Progress calculation logic
- WebSocket events for real-time updates
- Background worker for AI course generation

### Frontend (Web - Vite + shadcn-ui)
- Course list component with filters
- Course detail page
- Course edit form with drag-and-drop
- Progress visualization components
- Module/topic tree structure
- Modal dialogs for editing

### Frontend (Mobile - Expo)
- Native course list screens
- Course detail views
- Mobile-optimized editing
- Offline access to course data
- Sync mechanism for offline changes

### Database Schema
```
Course {
  id, userId, title, description, 
  difficulty, targetDate, createdAt, 
  isAIGenerated, archived
}

Module {
  id, courseId, title, order, 
  description, completed
}

Topic {
  id, moduleId, title, order, 
  content, completed, estimatedHours
}
```

## Subscription Tier Constraints

### Free Tier
- Max 2 AI-generated courses
- Unlimited manual courses
- Basic progress tracking

### Premium Tier
- Unlimited AI-generated courses
- Advanced analytics
- Resource recommendations
- Multi-device sync

## Acceptance Criteria

- [ ] User can view list of all courses
- [ ] User can create course manually
- [ ] AI can create course via chat command
- [ ] Course list shows progress percentage
- [ ] User can click course to view details
- [ ] Course detail page shows all modules and topics
- [ ] User can edit course title and description
- [ ] User can add/remove modules
- [ ] User can add/remove topics
- [ ] User can reorder modules and topics (drag-and-drop)
- [ ] Progress automatically updates when topics marked complete
- [ ] User can mark topics as completed
- [ ] User can mark entire modules as completed
- [ ] User can archive completed courses
- [ ] User can delete courses with confirmation
- [ ] Search works across course titles and descriptions
- [ ] Filters work (by progress, difficulty, date)
- [ ] Free tier users limited to 2 AI-generated courses
- [ ] Premium users have unlimited AI-generated courses
- [ ] Changes sync across web and mobile
- [ ] Real-time updates when AI creates course
- [ ] Offline mode works on mobile
- [ ] Notes can be linked to courses
- [ ] Tasks can be linked to courses

## API Endpoints

- `GET /api/courses` - List all user courses
- `POST /api/courses` - Create new course
- `GET /api/courses/:id` - Get course details
- `PUT /api/courses/:id` - Update course
- `DELETE /api/courses/:id` - Delete course
- `POST /api/courses/:id/archive` - Archive course
- `POST /api/courses/:id/modules` - Add module
- `PUT /api/courses/:id/modules/:moduleId` - Update module
- `DELETE /api/courses/:id/modules/:moduleId` - Delete module
- `POST /api/courses/:id/modules/:moduleId/topics` - Add topic
- `PUT /api/courses/:id/modules/:moduleId/topics/:topicId` - Update topic
- `DELETE /api/courses/:id/modules/:moduleId/topics/:topicId` - Delete topic
- `POST /api/courses/:id/progress` - Update progress
- `GET /api/courses/:id/analytics` - Get course analytics

## UI Components

- CourseList
- CourseCard
- CourseDetail
- CourseEditor
- ModuleList
- TopicList
- ProgressBar
- ProgressChart
- CourseFilters
- CourseSearch
- ArchiveConfirmDialog
- DeleteConfirmDialog

## Dependencies

- AI Chat (for course generation)
- Dashboard (displays course tiles)
- Notes Module (linking)
- Goals Module (linking)
- Subscription System (tier limits)

## Performance Requirements

- Course list loads in < 1 second
- Course detail loads in < 500ms
- Search results appear in < 300ms
- Progress updates in < 200ms
- Support 100+ courses per user

## Security Considerations

- User can only access own courses
- Authorization checks on all endpoints
- Validate course structure before saving
- Rate limiting on AI course generation

## Testing Requirements

- Unit tests for course logic
- Integration tests for CRUD operations
- E2E tests for course creation and editing flows
- Progress calculation tests
- Tier limit enforcement tests
- Offline sync tests for mobile

## Estimated Effort
Large - 3-4 sprints

## Related Issues
- AI Chat (generates courses)
- Dashboard (displays courses)
- Goals Module (course-goal linking)
- Notes Module (course-note linking)
- Subscription System (tier limits)
