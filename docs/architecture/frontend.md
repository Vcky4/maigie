# Dashboard & Frontend Organization (Detailed)

## Dashboard Components (Web + Mobile)

* Header: quick search, user menu, notifications
* Left nav: Courses, Goals, Resources, Schedule, AI Assistant
* Center: Today view (schedule blocks), Active Goal(s), Quick actions (add note, start study session)
* Right / widgets: Forecast (AI estimate), Reminders, Recent resources

## Data Displayed

* Courses: progress percentage, next session, recent notes
* Goals: progress bars with milestone markers
* Resources: starred, recommended (AI), recent
* Schedules: today timeline with collapsing time blocks
* Forecast: "If you study X hours/day you will reach Y% by DATE"

## Interaction Patterns

* Click a course → course detail page (modules, notes, recommended resources)
* Chat assistant modal/panel → context-aware (auto-select active course or goal)
* Quick-add from dashboard: create note, schedule block, or goal

## UI Tech Notes

* Web: Vite app with shadcn-ui components, Tailwind CSS configuration
* Mobile: Expo + React Navigation; reuse UI tokens via `libs/ui` and small platform-specific components
* Shared API client auto-generated from OpenAPI schema (fastapi can provide `/openapi.json`) and kept in `libs/types`.

