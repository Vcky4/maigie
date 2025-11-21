# GitHub Issue Creation Guide

This guide provides instructions for creating GitHub issues from the markdown files in this directory.

## Quick Reference

| Issue # | Title | Priority | File | Labels |
|---------|-------|----------|------|--------|
| 001 | Authentication & Onboarding | Critical | 001-authentication-onboarding.md | `authentication`, `onboarding`, `user-experience`, `mvp` |
| 002 | AI Chat (Text + Voice) | Critical | 002-ai-chat-text-voice.md | `ai`, `chat`, `voice`, `core-feature`, `mvp` |
| 003 | Dashboard Module | High | 003-dashboard-module.md | `dashboard`, `ui`, `core-feature`, `mvp` |
| 004 | Courses Module | High | 004-courses-module.md | `courses`, `learning`, `core-feature`, `mvp` |
| 005 | Goals Module | High | 005-goals-module.md | `goals`, `productivity`, `core-feature`, `mvp` |
| 006 | Scheduling Module | High | 006-scheduling-module.md | `scheduling`, `calendar`, `time-management`, `core-feature`, `mvp` |
| 007 | Notes Module | Medium | 007-notes-module.md | `notes`, `content`, `productivity`, `mvp` |
| 008 | Resource Recommendations | Medium | 008-resource-recommendations.md | `resources`, `recommendations`, `ai`, `learning` |
| 009 | Subscription & Billing System | High | 009-subscription-billing.md | `subscription`, `billing`, `monetization`, `payment`, `mvp` |
| 010 | Analytics & Progress Tracking | Medium | 010-analytics-progress-tracking.md | `analytics`, `tracking`, `insights`, `visualization` |
| 011 | Backend Infrastructure | Critical | 011-backend-infrastructure.md | `backend`, `infrastructure`, `architecture`, `api`, `mvp` |
| 012 | Non-Functional Requirements | High | 012-non-functional-requirements.md | `performance`, `scalability`, `accessibility`, `reliability`, `quality` |

## Method 1: Using GitHub CLI

The GitHub CLI (`gh`) is the fastest way to create issues programmatically.

### Prerequisites
```bash
# Install GitHub CLI (if not already installed)
# Visit: https://cli.github.com/

# Authenticate
gh auth login
```

### Create a Single Issue
```bash
# Example: Create Authentication & Onboarding issue
gh issue create \
  --repo Vcky4/maigie \
  --title "Authentication & Onboarding" \
  --body-file issues/001-authentication-onboarding.md \
  --label "authentication,onboarding,user-experience,mvp" \
  --milestone "MVP"
```

### Bulk Create All Issues
Create a script to automate the process:

```bash
#!/bin/bash
# create-issues.sh

REPO="Vcky4/maigie"
MILESTONE="MVP"  # Adjust as needed

# Issue 001
gh issue create --repo "$REPO" \
  --title "Authentication & Onboarding" \
  --body-file issues/001-authentication-onboarding.md \
  --label "authentication,onboarding,user-experience,mvp" \
  --milestone "$MILESTONE"

# Issue 002
gh issue create --repo "$REPO" \
  --title "AI Chat (Text + Voice)" \
  --body-file issues/002-ai-chat-text-voice.md \
  --label "ai,chat,voice,core-feature,mvp" \
  --milestone "$MILESTONE"

# Issue 003
gh issue create --repo "$REPO" \
  --title "Dashboard Module" \
  --body-file issues/003-dashboard-module.md \
  --label "dashboard,ui,core-feature,mvp" \
  --milestone "$MILESTONE"

# Issue 004
gh issue create --repo "$REPO" \
  --title "Courses Module" \
  --body-file issues/004-courses-module.md \
  --label "courses,learning,core-feature,mvp" \
  --milestone "$MILESTONE"

# Issue 005
gh issue create --repo "$REPO" \
  --title "Goals Module" \
  --body-file issues/005-goals-module.md \
  --label "goals,productivity,core-feature,mvp" \
  --milestone "$MILESTONE"

# Issue 006
gh issue create --repo "$REPO" \
  --title "Scheduling Module" \
  --body-file issues/006-scheduling-module.md \
  --label "scheduling,calendar,time-management,core-feature,mvp" \
  --milestone "$MILESTONE"

# Issue 007
gh issue create --repo "$REPO" \
  --title "Notes Module" \
  --body-file issues/007-notes-module.md \
  --label "notes,content,productivity,mvp" \
  --milestone "$MILESTONE"

# Issue 008
gh issue create --repo "$REPO" \
  --title "Resource Recommendations" \
  --body-file issues/008-resource-recommendations.md \
  --label "resources,recommendations,ai,learning"

# Issue 009
gh issue create --repo "$REPO" \
  --title "Subscription & Billing System" \
  --body-file issues/009-subscription-billing.md \
  --label "subscription,billing,monetization,payment,mvp" \
  --milestone "$MILESTONE"

# Issue 010
gh issue create --repo "$REPO" \
  --title "Analytics & Progress Tracking" \
  --body-file issues/010-analytics-progress-tracking.md \
  --label "analytics,tracking,insights,visualization"

# Issue 011
gh issue create --repo "$REPO" \
  --title "Backend Infrastructure" \
  --body-file issues/011-backend-infrastructure.md \
  --label "backend,infrastructure,architecture,api,mvp" \
  --milestone "$MILESTONE"

# Issue 012
gh issue create --repo "$REPO" \
  --title "Non-Functional Requirements" \
  --body-file issues/012-non-functional-requirements.md \
  --label "performance,scalability,accessibility,reliability,quality" \
  --milestone "$MILESTONE"

echo "All issues created successfully!"
```

Run the script:
```bash
chmod +x create-issues.sh
./create-issues.sh
```

## Method 2: Using GitHub Web Interface

1. Navigate to: https://github.com/Vcky4/maigie/issues
2. Click "New Issue"
3. Open the corresponding markdown file from this directory
4. Copy the entire content
5. Paste into the issue description
6. Extract the title from the first heading
7. Add the labels mentioned in the file
8. Click "Submit new issue"
9. Repeat for each file

## Method 3: Using GitHub API

### Using cURL
```bash
# Set your GitHub token
TOKEN="your_github_personal_access_token"
REPO="Vcky4/maigie"

# Create an issue
curl -X POST \
  -H "Authorization: token $TOKEN" \
  -H "Accept: application/vnd.github.v3+json" \
  https://api.github.com/repos/$REPO/issues \
  -d '{
    "title": "Authentication & Onboarding",
    "body": "'"$(cat issues/001-authentication-onboarding.md)"'",
    "labels": ["authentication", "onboarding", "user-experience", "mvp"]
  }'
```

### Using Python Script
```python
#!/usr/bin/env python3
import os
import requests
import json

GITHUB_TOKEN = os.environ.get('GITHUB_TOKEN')
REPO = 'Vcky4/maigie'
BASE_URL = f'https://api.github.com/repos/{REPO}/issues'

issues = [
    {
        'title': 'Authentication & Onboarding',
        'file': '001-authentication-onboarding.md',
        'labels': ['authentication', 'onboarding', 'user-experience', 'mvp']
    },
    {
        'title': 'AI Chat (Text + Voice)',
        'file': '002-ai-chat-text-voice.md',
        'labels': ['ai', 'chat', 'voice', 'core-feature', 'mvp']
    },
    # Add remaining issues...
]

headers = {
    'Authorization': f'token {GITHUB_TOKEN}',
    'Accept': 'application/vnd.github.v3+json'
}

for issue in issues:
    with open(f'issues/{issue["file"]}', 'r') as f:
        body = f.read()
    
    data = {
        'title': issue['title'],
        'body': body,
        'labels': issue['labels']
    }
    
    response = requests.post(BASE_URL, headers=headers, json=data)
    
    if response.status_code == 201:
        print(f"✓ Created: {issue['title']}")
    else:
        print(f"✗ Failed: {issue['title']} - {response.status_code}")

print("Done!")
```

## Recommended Labels to Create First

Before creating issues, ensure these labels exist in your repository:

### Feature Categories
- `authentication`
- `ai`
- `chat`
- `voice`
- `dashboard`
- `ui`
- `courses`
- `goals`
- `scheduling`
- `calendar`
- `notes`
- `resources`
- `subscription`
- `billing`
- `analytics`
- `backend`
- `infrastructure`

### Priority/Status
- `mvp`
- `core-feature`
- `critical`
- `high-priority`
- `medium-priority`
- `low-priority`

### Quality Attributes
- `performance`
- `scalability`
- `accessibility`
- `security`
- `testing`

### Platform
- `web`
- `mobile`
- `api`

## Milestones to Create

Consider creating these milestones to organize issues:

1. **MVP Foundation** (Sprints 1-3)
   - Backend Infrastructure
   - Authentication
   - Basic Dashboard

2. **Core Features** (Sprints 4-8)
   - AI Chat
   - Courses Module
   - Goals Module
   - Scheduling Module

3. **Supporting Features** (Sprints 9-11)
   - Notes Module
   - Resource Recommendations
   - Analytics

4. **Monetization** (Sprints 12-14)
   - Subscription & Billing

5. **Quality & Polish**
   - Non-Functional Requirements
   - Testing
   - Documentation

## Project Board Setup

Consider creating a project board with these columns:

1. **Backlog** - All created issues
2. **Ready** - Issues ready for development
3. **In Progress** - Currently being worked on
4. **In Review** - Pending code review
5. **Testing** - In QA/testing
6. **Done** - Completed and merged

## Tips

1. **Create labels first** to avoid manual assignment later
2. **Create milestones** to group related issues
3. **Use project boards** for visual tracking
4. **Link related issues** using GitHub's issue reference syntax (#123)
5. **Assign team members** to issues as work begins
6. **Update issue templates** to match this structure for future issues
7. **Enable issue templates** in `.github/ISSUE_TEMPLATE/` directory

## Next Steps After Issue Creation

1. Review and prioritize issues with the team
2. Assign issues to appropriate milestones
3. Add issues to project boards
4. Assign initial owners/teams
5. Begin sprint planning
6. Start development following the recommended order in README.md

## Verification

After creating issues, verify:
- [ ] All 12 issues created successfully
- [ ] Labels applied correctly
- [ ] Milestones assigned appropriately
- [ ] Issues added to project board
- [ ] Cross-references between issues working
- [ ] Team members notified

---

For questions or issues with the creation process, please refer to the [GitHub Issues Documentation](https://docs.github.com/en/issues).
