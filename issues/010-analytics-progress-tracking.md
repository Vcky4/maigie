# Analytics & Progress Tracking

## Issue Type
Feature

## Priority
Medium

## Labels
- analytics
- tracking
- insights
- visualization

## Description

Implement comprehensive analytics and progress tracking system to monitor user activity, study patterns, AI usage, and provide insights for both users and product metrics.

## User Stories

### As a user:
- I want to see my study time and patterns
- I want to track my progress toward goals
- I want to understand my learning habits
- I want to see which courses I'm progressing in
- I want insights on my productivity

### As a product owner:
- I want to track AI usage patterns
- I want to monitor user retention
- I want to understand feature adoption
- I want to track subscription conversion funnel
- I want to identify areas for improvement

## Functional Requirements

### User-Facing Analytics

#### Study Analytics
- Total study time (daily, weekly, monthly)
- Study time by course
- Study time by subject
- Study streak tracking
- Most productive times of day
- Session duration averages

#### Progress Analytics
- Course completion rates
- Goal achievement rate
- Tasks completed over time
- Schedule adherence rate
- Learning pace trends

#### AI Usage Analytics
- AI messages sent
- Voice interactions
- AI features used most
- AI-generated content stats

#### Insights & Reports
- Weekly study report
- Monthly progress summary
- Personalized recommendations
- Achievement badges/milestones
- Comparison to goals

### Backend Analytics (Product Metrics)

#### User Behavior
- Daily/Monthly Active Users (DAU/MAU)
- Session length and frequency
- Feature usage rates
- User flow analysis
- Churn prediction

#### AI Metrics
- AI request volume
- LLM token usage
- Voice interaction volume
- Intent detection accuracy
- Response time metrics

#### Retention & Engagement
- User retention cohorts
- Feature adoption rates
- Time to first value
- Engagement scoring
- Re-engagement patterns

#### Subscription Funnel
- Free to Premium conversion rate
- Trial conversion rate (if applicable)
- Upgrade prompts effectiveness
- Cancellation reasons
- Lifetime value (LTV)

### Data Visualization

#### User Dashboard
- Interactive charts (line, bar, pie)
- Progress bars
- Heatmaps (study patterns)
- Trend indicators
- Goal progress visualization

#### Admin Dashboard
- Real-time metrics
- Historical trends
- Cohort analysis
- Funnel visualization
- A/B test results

## Technical Requirements

### Backend
- FastAPI analytics endpoints
- Event tracking system
- Data aggregation pipelines
- Time-series database (TimescaleDB or InfluxDB)
- Background workers for metric calculation
- Data export functionality

### Frontend (Web - Vite + shadcn-ui)
- Chart library (Chart.js or Recharts)
- Analytics dashboard page
- Progress visualization components
- Interactive filters
- Date range selectors

### Frontend (Mobile - Expo)
- Native chart rendering
- Progress screens
- Achievements view
- Weekly reports notification

### Event Tracking

#### User Events
- `study_session_start`
- `study_session_end`
- `course_progress_update`
- `goal_completed`
- `task_completed`
- `schedule_block_completed`
- `ai_message_sent`
- `voice_interaction`
- `resource_viewed`
- `note_created`

#### System Events
- `user_signup`
- `user_login`
- `subscription_created`
- `subscription_canceled`
- `feature_used`
- `error_occurred`

### Database Schema
```
AnalyticsEvent {
  id, userId, eventType,
  eventData, timestamp,
  sessionId, platform
}

StudySession {
  id, userId, startTime, endTime,
  duration, courseId, completed
}

UserMetrics {
  userId, date,
  studyMinutes, tasksCompleted,
  aiMessagesUsed, goalsAchieved
}

SubscriptionMetrics {
  date, newSubscriptions,
  cancellations, revenue,
  mrrChange, churnRate
}
```

## Backend Infrastructure Schema

## Acceptance Criteria

### User Analytics
- [ ] User can view total study time
- [ ] Study time charts display correctly
- [ ] Progress charts show accurate data
- [ ] Study streak calculation is correct
- [ ] Goal progress visualizes accurately
- [ ] Course completion rates display
- [ ] AI usage statistics are accurate
- [ ] Weekly reports generate automatically
- [ ] User can export their data
- [ ] Charts are interactive and responsive
- [ ] Mobile analytics display properly

### Backend Analytics
- [ ] All user events are tracked
- [ ] Event data is stored correctly
- [ ] Metrics calculate accurately
- [ ] DAU/MAU metrics are correct
- [ ] Retention cohorts are accurate
- [ ] Subscription funnel tracks properly
- [ ] Real-time dashboards update
- [ ] Historical data is queryable
- [ ] Data aggregation is performant
- [ ] Admin can export analytics data

## API Endpoints

### User Analytics
- `GET /api/analytics/study-time` - Get study time stats
- `GET /api/analytics/progress` - Get progress stats
- `GET /api/analytics/goals` - Get goal achievement stats
- `GET /api/analytics/ai-usage` - Get AI usage stats
- `GET /api/analytics/insights` - Get personalized insights
- `GET /api/analytics/streak` - Get study streak info
- `GET /api/analytics/report` - Generate periodic report
- `POST /api/analytics/export` - Export user data

### Admin Analytics
- `GET /api/admin/analytics/overview` - Overview metrics
- `GET /api/admin/analytics/users` - User metrics
- `GET /api/admin/analytics/retention` - Retention analysis
- `GET /api/admin/analytics/subscriptions` - Subscription metrics
- `GET /api/admin/analytics/ai` - AI usage metrics
- `GET /api/admin/analytics/features` - Feature adoption

## UI Components

### User-Facing
- StudyTimeChart
- ProgressChart
- StreakDisplay
- GoalProgressRing
- InsightCard
- WeeklyReportView
- AchievementBadge
- TrendIndicator

### Admin
- MetricCard
- LineChart
- BarChart
- PieChart
- HeatMap
- FunnelChart
- CohortTable
- RealtimeMetric

## Privacy & Data Handling

- Anonymize data for product analytics
- User data export (GDPR compliance)
- Data retention policies
- User consent for tracking
- Privacy-preserving analytics
- Opt-out mechanism

## Performance Requirements

- Event tracking < 50ms overhead
- Analytics queries < 1 second
- Chart rendering < 500ms
- Real-time dashboard updates < 2 seconds
- Support millions of events
- Data aggregation in background

## Security Considerations

- User can only view own analytics
- Admin analytics require special role
- Rate limiting on analytics queries
- Secure data export
- Audit logging for admin access

## Testing Requirements

- Unit tests for metric calculations
- Integration tests for event tracking
- E2E tests for analytics flows
- Data accuracy validation tests
- Performance tests for queries
- Chart rendering tests

## Third-Party Integrations (Optional)

- Google Analytics
- Mixpanel
- Amplitude
- Segment
- Custom analytics platform

## Estimated Effort
Medium - 2-3 sprints

## Related Issues
- All feature modules (event tracking)
- Dashboard (analytics display)
- Subscription System (conversion tracking)
