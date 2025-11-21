# Maigie Project Issues

This directory contains detailed issue specifications derived from the Product Requirements Document (PRD) located at `docs/requirements/maigie_prd.md`.

## Overview

Each issue file represents a major feature or component of the Maigie application. These issues are designed to be comprehensive, actionable, and serve as a blueprint for development.

## Issue Structure

Each issue follows a consistent structure:

- **Issue Type**: Feature, Bug, Enhancement, etc.
- **Priority**: Critical, High, Medium, Low
- **Labels**: Categorization tags
- **Description**: High-level overview
- **User Stories**: User-centric scenarios
- **Functional Requirements**: Detailed feature specifications
- **Technical Requirements**: Implementation details
- **Acceptance Criteria**: Testable conditions for completion
- **API Endpoints**: Backend endpoints (if applicable)
- **UI Components**: Frontend components (if applicable)
- **Database Schema**: Data models (if applicable)
- **Dependencies**: Related issues and external dependencies
- **Performance Requirements**: Speed and efficiency targets
- **Security Considerations**: Security measures
- **Testing Requirements**: Test coverage needs
- **Estimated Effort**: Development time estimate
- **Related Issues**: Cross-references to other issues

## Issues List

### Core Features (MVP)

1. **[001-authentication-onboarding.md](./001-authentication-onboarding.md)** - Critical
   - User authentication (email/password, OAuth)
   - Onboarding flow with preference setting
   - Session management

2. **[002-ai-chat-text-voice.md](./002-ai-chat-text-voice.md)** - Critical
   - Text-based conversational AI
   - Voice input/output capabilities
   - Intent detection and action execution
   - Real-time WebSocket communication

3. **[003-dashboard-module.md](./003-dashboard-module.md)** - High
   - Central hub with all modules
   - Real-time updates via WebSocket
   - Quick access to courses, goals, schedules, resources
   - Daily forecast and reminders

4. **[004-courses-module.md](./004-courses-module.md)** - High
   - AI-generated and manual course creation
   - Module and topic structure
   - Progress tracking
   - Course editing capabilities

5. **[005-goals-module.md](./005-goals-module.md)** - High
   - Goal creation (AI and manual)
   - Subtask management
   - Progress tracking
   - Deadline management

6. **[006-scheduling-module.md](./006-scheduling-module.md)** - High
   - Calendar views (daily, weekly, monthly)
   - AI-generated schedules
   - Manual time blocking
   - Conflict detection
   - Recurring schedules
   - Reminders

7. **[007-notes-module.md](./007-notes-module.md)** - Medium
   - Rich text note editor
   - AI summarization
   - Voice-to-text
   - Course linking
   - Full-text search

8. **[008-resource-recommendations.md](./008-resource-recommendations.md)** - Medium
   - AI-powered recommendations
   - Resource library
   - Progress tracking
   - Rating system

9. **[009-subscription-billing.md](./009-subscription-billing.md)** - High
   - Free and Premium tiers
   - Stripe payment integration
   - Subscription management
   - Tier-based feature restrictions
   - Billing history

### Infrastructure & Quality

10. **[010-analytics-progress-tracking.md](./010-analytics-progress-tracking.md)** - Medium
    - User analytics (study time, progress)
    - Product metrics (DAU/MAU, retention)
    - Visualization dashboards
    - Insights and reports

11. **[011-backend-infrastructure.md](./011-backend-infrastructure.md)** - Critical
    - FastAPI setup
    - Prisma ORM and database schema
    - WebSocket infrastructure
    - Background workers
    - AI service integration
    - Caching with Redis
    - Authentication and security

12. **[012-non-functional-requirements.md](./012-non-functional-requirements.md)** - High
    - Performance targets
    - Scalability requirements
    - Security standards
    - Accessibility (WCAG 2.1 AA)
    - Reliability and uptime
    - Monitoring and observability

## Using These Issues

### For Creating GitHub Issues

These markdown files can be used to create GitHub issues in several ways:

#### Option 1: Manual Creation
1. Open the issue file
2. Copy the content
3. Create a new GitHub issue
4. Paste the content
5. Add appropriate labels and assignees

#### Option 2: GitHub CLI
```bash
# Example for creating an issue from a file
gh issue create \
  --title "Authentication & Onboarding" \
  --body-file issues/001-authentication-onboarding.md \
  --label "authentication,onboarding,mvp" \
  --assignee "@me"
```

#### Option 3: Automated Script
Create a script to bulk-create issues from all files in this directory.

### Development Workflow

1. **Planning Phase**
   - Review issue requirements
   - Identify dependencies
   - Estimate effort
   - Prioritize work

2. **Development Phase**
   - Use acceptance criteria as checklist
   - Implement technical requirements
   - Build UI components
   - Create API endpoints

3. **Testing Phase**
   - Validate against acceptance criteria
   - Run tests per testing requirements
   - Perform security checks
   - Test performance targets

4. **Review Phase**
   - Code review
   - Security review
   - Accessibility review
   - Performance review

## Priority Guidelines

- **Critical**: Must be completed for MVP launch
- **High**: Important features for initial release
- **Medium**: Nice-to-have features for initial release
- **Low**: Future enhancements

## Dependencies Graph

```
Backend Infrastructure (011)
    ├── Authentication & Onboarding (001)
    │   ├── AI Chat (002)
    │   │   ├── Courses Module (004)
    │   │   ├── Goals Module (005)
    │   │   └── Scheduling Module (006)
    │   ├── Dashboard (003)
    │   │   ├── Courses Module (004)
    │   │   ├── Goals Module (005)
    │   │   ├── Scheduling Module (006)
    │   │   └── Resource Recommendations (008)
    │   ├── Notes Module (007)
    │   ├── Subscription & Billing (009)
    │   └── Analytics (010)
    └── Non-Functional Requirements (012) - Applies to all
```

## Recommended Development Order

### Phase 1: Foundation (Sprints 1-3)
1. Backend Infrastructure (011)
2. Authentication & Onboarding (001)
3. Basic Dashboard (003)

### Phase 2: Core Features (Sprints 4-8)
4. AI Chat (002)
5. Courses Module (004)
6. Goals Module (005)
7. Scheduling Module (006)

### Phase 3: Supporting Features (Sprints 9-11)
8. Notes Module (007)
9. Resource Recommendations (008)
10. Analytics (010)

### Phase 4: Monetization (Sprints 12-14)
11. Subscription & Billing (009)
12. Tier enforcement across all modules

### Ongoing: Quality & Performance
- Non-Functional Requirements (012) - Continuous

## Contributing

When working on these issues:

1. **Reference the issue** in your commits and PRs
2. **Update the issue** if requirements change
3. **Check off acceptance criteria** as you complete them
4. **Add new issues** for bugs or enhancements discovered
5. **Link related issues** for better tracking

## Issue Template

When creating new issues, use this structure:

```markdown
# [Feature Name]

## Issue Type
[Feature/Bug/Enhancement]

## Priority
[Critical/High/Medium/Low]

## Labels
- label1
- label2

## Description
[High-level overview]

## User Stories
[User-centric scenarios]

## Functional Requirements
[Detailed specifications]

## Technical Requirements
[Implementation details]

## Acceptance Criteria
- [ ] Criterion 1
- [ ] Criterion 2

## Dependencies
[Related issues]

## Estimated Effort
[Time estimate]
```

## Questions or Feedback

If you have questions about any issue or need clarification on requirements:

1. Comment on the GitHub issue (once created)
2. Reach out to the product team
3. Refer to the full PRD at `docs/requirements/maigie_prd.md`

---

**Note**: These issues are derived from the PRD and represent the initial requirements. They may be refined and updated as development progresses and new insights are gained.
