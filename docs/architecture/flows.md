# Example Flows (Detailed)

## Flow 1: "Chat about what courses I'm offering"

1. User opens AI Assistant (context includes: user owned courses & selected course)
2. Client sends `{ message: "Summarize the courses I'm offering", context: { scope: "ownedCourses" } }` to `/api/v1/ai/chat`
3. Backend retrieves user's course list, composes a retrieval buffer, and calls LLM.
4. LLM returns structured reply: summary + suggestions (e.g. gaps in syllabus)
5. Backend persists conversation and returns response. Optionally UI shows quick actions (edit course, add module)

## Flow 2: "Set goal and get recommended resources"

1. User creates goal via `POST /goals`
2. Backend creates Goal and enqueues `recommendation` job.
3. Worker collects course context, notes, and queries vector DB for resources, then calls LLM to synthesize a prioritized list.
4. Recommendations saved to `resources` with `recommendation_source=ai` and surfaced on dashboard.

## Flow 3: "Daily Forecast"

1. Daily cron runs per user: gather historical study session durations, task completion rates.
2. AI worker predicts completion likelihood for active goals and returns a calendar forecast for the next 7/14/30 days.
3. Dashboard shows forecast widget with recommended study time per day to meet target date.

