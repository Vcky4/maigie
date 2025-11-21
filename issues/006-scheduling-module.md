# Scheduling Module

## Issue Type
Feature

## Priority
High

## Labels
- scheduling
- calendar
- time-management
- core-feature
- mvp

## Description

Implement the scheduling and calendar system where users can view, create, and manage their study schedules. Supports AI-generated schedules, manual time blocks, calendar views, and conflict detection.

## User Stories

### As a user:
- I want to see my daily and weekly study schedule
- I want AI to generate optimal study schedules based on my goals
- I want to manually add study blocks to my calendar
- I want to be notified about upcoming schedule blocks
- I want the system to detect scheduling conflicts
- I want recurring schedule support for regular study sessions

## Functional Requirements

### Schedule Structure
- ScheduleBlock contains:
  - Title
  - Description
  - Start time
  - End time
  - Date
  - Type (Study, Break, Assignment, Exam, etc.)
  - Related course (optional)
  - Related goal (optional)
  - Location (optional)
  - Recurrence rule (optional)
  - Reminder settings
  - AI-generated flag

### Calendar Views
- **Daily view**: Timeline with hourly blocks
- **Weekly view**: 7-day grid
- **Monthly view**: Calendar month overview
- **Agenda view**: List of upcoming blocks

### Schedule Creation
- **AI-generated**: AI creates optimal schedule based on goals and deadlines
- **Manual creation**: User creates blocks via form
- **Template-based**: Recurring schedule templates
- **Quick add**: Fast entry for simple blocks

### Schedule Management
- View schedule in multiple formats
- Create new schedule blocks
- Edit existing blocks
- Delete blocks (with confirmation)
- Move blocks (drag-and-drop in calendar)
- Resize blocks (adjust duration)
- Mark blocks as completed
- Skip scheduled blocks

### AI Scheduling Features
- AI analyzes goals and deadlines
- AI suggests optimal time blocks
- AI considers user's time availability
- AI balances study time across subjects
- AI includes break periods
- AI reschedules when conflicts occur
- User can approve or dismiss suggestions

### Conflict Detection
- Detect overlapping schedule blocks
- Warn user before saving conflicting blocks
- Suggest alternative times
- Auto-resolution options

### Recurring Schedules
- Daily recurrence
- Weekly recurrence (specific days)
- Custom recurrence patterns
- Edit single instance or series
- Delete single instance or series

### Reminders
- Configurable reminder times (5min, 15min, 1hr, 1day before)
- Push notifications
- Email reminders (optional)
- In-app notifications

## Technical Requirements

### Backend
- FastAPI endpoints for CRUD operations
- Prisma model for ScheduleBlock
- Recurrence rule parsing and generation
- Conflict detection algorithm
- Background worker for reminder dispatch
- WebSocket events for real-time updates
- Calendar export (iCal format)

### Frontend (Web - Vite + shadcn-ui)
- Calendar component library integration
- Daily/weekly/monthly view components
- Schedule editor form
- Drag-and-drop functionality
- Conflict resolution UI
- Reminder configuration UI
- Time zone handling

### Frontend (Mobile - Expo)
- Native calendar screens
- Swipe gestures for navigation
- Quick add widget
- Push notification handling
- Calendar widget for home screen
- Offline schedule access

### Database Schema
```
ScheduleBlock {
  id, userId, title, description,
  startTime, endTime, date,
  type, location, isAIGenerated,
  courseId, goalId,
  recurrenceRule, parentBlockId,
  reminderMinutes, completed,
  createdAt
}

ScheduleReminder {
  id, blockId, sentAt, status
}
```

## Subscription Tier Constraints

### Free Tier
- Manual scheduling only
- Basic calendar views
- Limited reminders

### Premium Tier
- AI-generated schedules
- Advanced scheduling features
- Unlimited reminders
- Calendar integrations

## Acceptance Criteria

- [ ] User can view daily schedule
- [ ] User can view weekly schedule
- [ ] User can view monthly calendar
- [ ] User can create schedule block manually
- [ ] AI can generate complete schedule via chat
- [ ] User can edit schedule blocks
- [ ] User can delete schedule blocks
- [ ] User can drag-and-drop blocks to new times
- [ ] User can resize blocks to adjust duration
- [ ] System detects scheduling conflicts
- [ ] Conflict warning displays before saving
- [ ] User can create recurring schedules
- [ ] User can edit recurring schedule series
- [ ] User can delete single recurrence instance
- [ ] User can delete entire recurrence series
- [ ] User can set reminders for blocks
- [ ] Reminders are sent at specified times
- [ ] Push notifications work on mobile
- [ ] User can link blocks to courses
- [ ] User can link blocks to goals
- [ ] User can mark blocks as completed
- [ ] Completed blocks show visually distinct
- [ ] Free tier users have manual scheduling only
- [ ] Premium users have AI scheduling
- [ ] Changes sync across web and mobile
- [ ] Real-time updates when schedule changes
- [ ] Offline mode works on mobile
- [ ] Time zones handled correctly
- [ ] Calendar exports to iCal format

## API Endpoints

- `GET /api/schedule` - Get schedule blocks (with date range)
- `POST /api/schedule` - Create new block
- `GET /api/schedule/:id` - Get block details
- `PUT /api/schedule/:id` - Update block
- `DELETE /api/schedule/:id` - Delete block
- `POST /api/schedule/generate` - AI generate schedule
- `POST /api/schedule/:id/complete` - Mark block complete
- `GET /api/schedule/conflicts` - Check for conflicts
- `POST /api/schedule/recurring` - Create recurring blocks
- `PUT /api/schedule/recurring/:seriesId` - Update series
- `DELETE /api/schedule/recurring/:seriesId` - Delete series
- `GET /api/schedule/export` - Export calendar (iCal)

## UI Components

- CalendarGrid
- DailyTimeline
- WeeklyView
- MonthlyView
- AgendaList
- ScheduleBlockCard
- BlockEditor
- ConflictDialog
- RecurrenceEditor
- ReminderSettings
- QuickAddModal
- CompletionCheckbox

## Dependencies

- AI Chat (for schedule generation)
- Dashboard (displays schedule preview)
- Goals Module (schedule-goal linking)
- Courses Module (schedule-course linking)
- Subscription System (tier limits)
- Notification Service (reminders)

## Performance Requirements

- Schedule view loads in < 1 second
- Calendar rendering < 500ms
- Drag-and-drop smooth (60fps)
- Conflict detection < 200ms
- Support 1000+ blocks per user
- Real-time updates latency < 100ms

## Security Considerations

- User can only access own schedules
- Authorization checks on all endpoints
- Validate time ranges before saving
- Rate limiting on AI schedule generation

## Testing Requirements

- Unit tests for conflict detection
- Integration tests for CRUD operations
- E2E tests for scheduling flows
- Recurrence rule parsing tests
- Reminder dispatch tests
- Drag-and-drop interaction tests
- Time zone handling tests
- Offline sync tests for mobile

## Estimated Effort
Large - 4-5 sprints

## Related Issues
- AI Chat (generates schedules)
- Dashboard (displays schedule)
- Goals Module (schedule-goal integration)
- Courses Module (schedule-course integration)
- Subscription System (tier limits)
- Notification Service (reminders)
