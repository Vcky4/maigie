# Goals Module

## Issue Type
Feature

## Priority
High

## Labels
- goals
- productivity
- core-feature
- mvp

## Description

Implement the goals management system where users can create, track, and manage their study goals. Goals can be AI-generated or manually created, include subtasks, deadlines, and progress tracking.

## User Stories

### As a user:
- I want to set study goals with specific deadlines
- I want AI to suggest and create goals based on my conversation
- I want to break down goals into subtasks
- I want to track my progress toward each goal
- I want to see how goals relate to my courses and schedules
- I want reminders for upcoming goal deadlines

## Functional Requirements

### Goal Structure
- Goal contains:
  - Title
  - Description
  - Target date/deadline
  - Priority level (High, Medium, Low)
  - Status (Not Started, In Progress, Completed, Abandoned)
  - Progress percentage
  - Subtasks/tasks list
  - Related courses (optional)
  - Tags
  - AI-generated flag

### Goal Creation
- **AI-generated**: AI creates goals via chat interaction
- **Manual creation**: User creates goals via form
- **Smart suggestions**: AI suggests goals based on course enrollment

### Goal Management
- View all goals (list or kanban view)
- Filter by status, priority, deadline
- Sort by various criteria
- Mark goals as complete
- Archive or delete goals
- Duplicate goals for recurring objectives

### Goal Detail View
- Display full goal information
- Show subtasks with checkboxes
- Display progress visualization
- Show related courses and schedules
- Timeline view of progress history
- Edit goal inline

### Subtasks
- Add/remove subtasks
- Mark subtasks complete
- Reorder subtasks
- Set individual subtask deadlines (optional)
- Automatic progress calculation based on subtasks

### Progress Tracking
- Manual progress updates
- Automatic progress from subtask completion
- Visual progress indicators
- Progress history timeline
- Milestone celebrations

### AI Features
- AI can create goals from conversation
- AI suggests goal breakdowns
- AI recommends related resources
- AI provides progress insights
- AI warns about deadline risks

## Technical Requirements

### Backend
- FastAPI endpoints for CRUD operations
- Prisma models for Goal and Task
- Progress calculation logic
- Deadline monitoring background worker
- WebSocket events for real-time updates
- Notification service integration

### Frontend (Web - Vite + shadcn-ui)
- Goal list with filters
- Goal detail view
- Goal editor form
- Kanban board view (optional)
- Progress charts
- Subtask management UI

### Frontend (Mobile - Expo)
- Native goal list screens
- Goal detail views
- Quick add goal widget
- Push notifications for deadlines
- Offline goal management

### Database Schema
```
Goal {
  id, userId, title, description,
  targetDate, priority, status,
  progress, isAIGenerated,
  createdAt, completedAt
}

Task {
  id, goalId, title, description,
  completed, order, deadline
}

GoalCourse {
  id,           // unique identifier for the junction entry
  goalId,       // references Goal.id
  courseId,     // references Course.id
  createdAt     // timestamp for when the link was created
}
```

## Subscription Tier Constraints

### Free Tier
- Max 2 active goals
- Basic progress tracking
- Limited AI suggestions

### Premium Tier
- Unlimited active goals
- Advanced analytics
- Full AI assistance
- Custom reminders

## Acceptance Criteria

- [ ] User can view list of all goals
- [ ] User can create goal manually
- [ ] AI can create goal via chat command
- [ ] Goal list shows status, progress, and deadline
- [ ] User can click goal to view details
- [ ] Goal detail page shows all information and subtasks
- [ ] User can edit goal title, description, and deadline
- [ ] User can add/remove subtasks
- [ ] User can mark subtasks as completed
- [ ] Progress auto-calculates from subtask completion
- [ ] User can manually update progress percentage
- [ ] User can mark goal as complete
- [ ] User can change goal priority
- [ ] User can link goals to courses
- [ ] User can filter goals by status
- [ ] User can filter goals by priority
- [ ] User can sort goals by deadline
- [ ] User can archive completed goals
- [ ] User can delete goals with confirmation
- [ ] Free tier users limited to 2 active goals
- [ ] Premium users have unlimited active goals
- [ ] Deadline reminders are sent via notification
- [ ] AI warns about at-risk goals
- [ ] Changes sync across web and mobile
- [ ] Real-time updates when AI creates goal
- [ ] Offline mode works on mobile

## API Endpoints

- `GET /api/goals` - List all user goals
- `POST /api/goals` - Create new goal
- `GET /api/goals/:id` - Get goal details
- `PUT /api/goals/:id` - Update goal
- `DELETE /api/goals/:id` - Delete goal
- `POST /api/goals/:id/complete` - Mark goal complete
- `POST /api/goals/:id/archive` - Archive goal
- `POST /api/goals/:id/tasks` - Add subtask
- `PUT /api/goals/:id/tasks/:taskId` - Update subtask
- `DELETE /api/goals/:id/tasks/:taskId` - Delete subtask
- `POST /api/goals/:id/tasks/:taskId/toggle` - Toggle subtask completion
- `POST /api/goals/:id/progress` - Update progress
- `GET /api/goals/analytics` - Get goals analytics

## UI Components

- GoalList
- GoalCard
- GoalDetail
- GoalEditor
- TaskList
- TaskItem
- ProgressBar
- ProgressChart
- GoalFilters
- PriorityBadge
- StatusBadge
- DeadlineWarning
- CompletionCelebration

## Dependencies

- AI Chat (for goal generation)
- Dashboard (displays goal tiles)
- Courses Module (goal-course linking)
- Scheduling Module (goal-schedule integration)
- Subscription System (tier limits)
- Notification Service (deadline reminders)

## Performance Requirements

- Goal list loads in < 1 second
- Goal detail loads in < 500ms
- Progress updates in < 200ms
- Real-time updates latency < 100ms
- Support 50+ goals per user

## Security Considerations

- User can only access own goals
- Authorization checks on all endpoints
- Validate goal data before saving
- Rate limiting on AI goal generation

## Testing Requirements

- Unit tests for goal logic
- Integration tests for CRUD operations
- E2E tests for goal creation and management
- Progress calculation tests
- Tier limit enforcement tests
- Notification delivery tests
- Offline sync tests for mobile

## Estimated Effort
Medium - 2-3 sprints

## Related Issues
- AI Chat (generates goals)
- Dashboard (displays goals)
- Courses Module (goal-course linking)
- Scheduling Module (goal-schedule integration)
- Subscription System (tier limits)
- Notification Service (reminders)
