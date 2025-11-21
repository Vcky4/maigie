# Resource Recommendations

## Issue Type
Feature

## Priority
Medium

## Labels
- resources
- recommendations
- ai
- learning

## Description

Implement the resource recommendation engine that suggests relevant learning materials (videos, articles, courses, books) based on user's courses, topics, and learning behavior.

## User Stories

### As a user:
- I want to discover relevant learning resources for my courses
- I want AI to recommend resources based on my learning style
- I want to save favorite resources
- I want to track which resources I've completed
- I want to rate and review resources
- I want personalized recommendations that improve over time

## Functional Requirements

### Resource Structure
- Resource contains:
  - Title
  - Description
  - Type (Video, Article, Course, Book, Tutorial, etc.)
  - URL/link
  - Source (YouTube, Coursera, etc.)
  - Difficulty level
  - Estimated time
  - Topics/tags
  - Rating (if available)
  - Thumbnail/image

### Recommendation Engine
- AI selects based on:
  - Current course topics
  - User's difficulty level
  - Past learning behavior
  - Resources completed
  - Resources rated highly
  - Study goals and deadlines
  - Time availability

### Resource Discovery
- View recommended resources
- Browse by category/topic
- Search for specific resources
- Filter by type, difficulty, duration
- Sort by relevance, rating, popularity

### Resource Management
- Save resources to personal library
- Mark resources as completed
- Rate resources (1-5 stars)
- Add notes to resources
- Share resources (optional)
- Remove from library

### Resource Library
- Personal collection of saved resources
- Organized by course/topic
- Progress tracking
- Completion statistics
- Recently viewed

### AI Features
- **Smart recommendations**: Context-aware suggestions
- **Learning path**: Sequential resource recommendations
- **Gap analysis**: Identify missing knowledge areas
- **Difficulty matching**: Resources matched to skill level
- **Time-based**: Suggest resources based on available time

## Technical Requirements

### Backend
- FastAPI endpoints for resources
- Prisma models for Resource and UserResource
- Recommendation algorithm
- External API integrations (YouTube, etc.)
- Search index for resources
- Content scraping/enrichment
- Caching for performance

### Frontend (Web - Vite + shadcn-ui)
- Resource card grid
- Resource detail view
- Search and filter UI
- Library view
- Rating component
- Progress tracking

### Frontend (Mobile - Expo)
- Native resource list
- Resource detail screens
- In-app browser for content
- Offline saved resources list

### Database Schema
```
Resource {
  id, title, description, type,
  url, source, difficulty,
  estimatedMinutes, thumbnailUrl,
  externalRating, createdAt
}

ResourceTopic {
  resourceId, topic
}

UserResource {
  userId, resourceId, savedAt,
  completed, completedAt,
  userRating, notes,
  progressPercent
}

ResourceRecommendation {
  userId, resourceId, score,
  reason, createdAt, dismissed
}
```

> **Note:** The `ResourceTopic` and `ResourceRecommendation` models defined above must also be included in the comprehensive backend infrastructure schema in [issue 011 (011-backend-infrastructure.md)](011-backend-infrastructure.md) for consistency.
## Subscription Tier Constraints

### Free Tier
- Basic recommendations (10/week)
- Limited resource library
- Standard search

### Premium Tier
- Unlimited recommendations
- Unlimited library size
- Advanced search and filters
- Priority resource updates
- Personalized learning paths

## Acceptance Criteria

- [ ] User can view recommended resources
- [ ] Recommendations are relevant to user's courses
- [ ] User can save resources to library
- [ ] User can mark resources as completed
- [ ] User can rate resources
- [ ] User can search for resources
- [ ] Search results are relevant
- [ ] User can filter by type, difficulty, duration
- [ ] Resource library shows saved items
- [ ] Progress tracking works for resources
- [ ] AI recommendations improve over time
- [ ] User can dismiss irrelevant recommendations
- [ ] Resource details display correctly
- [ ] External links open properly
- [ ] Free tier users see 10 recommendations/week
- [ ] Premium users see unlimited recommendations
- [ ] Thumbnail images load quickly
- [ ] Resource cards are visually appealing
- [ ] Mobile in-app browser works
- [ ] Offline library access on mobile

## API Endpoints

- `GET /api/resources/recommendations` - Get personalized recommendations
- `GET /api/resources/search` - Search resources
- `GET /api/resources/:id` - Get resource details
- `POST /api/resources/:id/save` - Save to library
- `DELETE /api/resources/:id/save` - Remove from library
- `POST /api/resources/:id/complete` - Mark complete
- `POST /api/resources/:id/rate` - Rate resource
- `GET /api/resources/library` - Get user's library
- `POST /api/resources/:id/dismiss` - Dismiss recommendation
- `GET /api/resources/topics/:topic` - Get resources by topic

## UI Components

- ResourceCard
- ResourceGrid
- ResourceDetail
- ResourceSearch
- ResourceFilters
- LibraryView
- RatingWidget
- ProgressIndicator
- RecommendationFeed

## Dependencies

- AI Chat (for recommendations)
- Courses Module (topic-based recommendations)
- Dashboard (displays resource tiles)
- External APIs (YouTube, etc.)

## Recommendation Algorithm

### Scoring Factors:
1. Topic relevance (40%)
2. Difficulty match (20%)
3. User behavior history (20%)
4. Resource quality/rating (10%)
5. Time availability (10%)

### Learning:
- Track which recommendations are saved
- Track completion rates
- Track user ratings
- Adjust scoring weights based on feedback

## Performance Requirements

- Recommendations load in < 1 second
- Search results in < 500ms
- Resource detail loads in < 300ms
- Support 10,000+ resources in database
- Recommendation calculation < 200ms

## Security Considerations

- User can only access own library
- Validate external URLs
- Rate limiting on searches
- Content filtering for inappropriate material

## Testing Requirements

- Unit tests for recommendation algorithm
- Integration tests for resource operations
- E2E tests for discovery and saving flows
- Recommendation relevance tests
- Search accuracy tests

## External Integrations

- YouTube Data API (video resources)
- Course aggregators APIs
- Article/blog aggregators
- Future: Coursera, Udemy, Khan Academy APIs

## Estimated Effort
Medium - 2-3 sprints

## Related Issues
- AI Chat (recommendation requests)
- Courses Module (topic extraction)
- Dashboard (resource display)
- Subscription System (tier limits)
