# Backend Infrastructure

## Issue Type
Feature

## Priority
Critical

## Labels
- backend
- infrastructure
- architecture
- api
- mvp

## Description

Implement the complete backend infrastructure for Maigie including FastAPI setup, database schema, authentication, real-time communication, background workers, and AI integration services.

## User Stories

### As a developer:
- I want a scalable backend architecture
- I want well-documented API endpoints
- I want reliable real-time updates
- I want efficient background job processing
- I want comprehensive error handling

### As a user:
- I want fast API responses
- I want reliable real-time updates
- I want my data to be secure
- I want the system to be always available

## Functional Requirements

### Core Backend Stack
- **Framework**: FastAPI
- **ORM**: Prisma
- **Database**: PostgreSQL
- **Cache**: Redis
- **Message Queue**: Redis/Celery
- **WebSockets**: FastAPI WebSocket or Socket.io
- **Search**: PostgreSQL full-text or Elasticsearch (optional)

### API Architecture

#### RESTful Endpoints
- Authentication & user management
- Courses CRUD
- Goals CRUD
- Schedule CRUD
- Notes CRUD
- Resources
- Subscription management
- Analytics
- Admin endpoints

#### WebSocket Connections
- Real-time chat
- Dashboard updates
- Notification delivery
- Collaboration features (future)

#### GraphQL (Optional)
- Flexible data querying
- Reduced over-fetching
- Better mobile performance

### Database Schema

#### Core Models
```prisma
model User {
  id            String   @id @default(uuid())
  email         String   @unique
  passwordHash  String?
  name          String?
  provider      String?  // oauth provider
  providerId    String?
  tier          Tier     @default(FREE)
  timezone      String?
  preferences   Json?
  createdAt     DateTime @default(now())
  updatedAt     DateTime @updatedAt
}

model Course {
  id              String    @id @default(uuid())
  userId          String
  title           String
  description     String?
  difficulty      String?
  targetDate      DateTime?
  progress        Float     @default(0)
  isAIGenerated   Boolean   @default(false)
  archived        Boolean   @default(false)
  createdAt       DateTime  @default(now())
  updatedAt       DateTime  @updatedAt
}

model Module {
  id          String   @id @default(uuid())
  courseId    String
  title       String
  order       Int
  description String?
  completed   Boolean  @default(false)
  createdAt   DateTime @default(now())
}

model Topic {
  id              String   @id @default(uuid())
  moduleId        String
  title           String
  order           Int
  content         String?
  completed       Boolean  @default(false)
  estimatedHours  Float?
  createdAt       DateTime @default(now())
}

model Goal {
  id              String    @id @default(uuid())
  userId          String
  title           String
  description     String?
  targetDate      DateTime?
  priority        Priority  @default(MEDIUM)
  status          Status    @default(NOT_STARTED)
  progress        Float     @default(0)
  isAIGenerated   Boolean   @default(false)
  createdAt       DateTime  @default(now())
  completedAt     DateTime?
}

model Task {
  id          String   @id @default(uuid())
  goalId      String
  title       String
  description String?
  completed   Boolean  @default(false)
  order       Int
  deadline    DateTime?
}

model ScheduleBlock {
  id              String    @id @default(uuid())
  userId          String
  title           String
  description     String?
  startTime       DateTime
  endTime         DateTime
  type            String?
  courseId        String?
  goalId          String?
  recurrenceRule  String?
  reminderMinutes Int?
  completed       Boolean   @default(false)
  isAIGenerated   Boolean   @default(false)
  createdAt       DateTime  @default(now())
}

model Note {
  id        String   @id @default(uuid())
  userId    String
  title     String
  content   String
  summary   String?
  courseId  String?
  topicId   String?
  archived  Boolean  @default(false)
  createdAt DateTime @default(now())
  updatedAt DateTime @updatedAt
}

model Resource {
  id              String   @id @default(uuid())
  title           String
  description     String?
  type            String
  url             String
  source          String?
  difficulty      String?
  estimatedMinutes Int?
  thumbnailUrl    String?
  createdAt       DateTime @default(now())
}

model UserResource {
  userId       String
  resourceId   String
  savedAt      DateTime @default(now())
  completed    Boolean  @default(false)
  completedAt  DateTime?
  userRating   Int?
  notes        String?
  progress     Float?
}

model Subscription {
  id                    String    @id @default(uuid())
  userId                String    @unique
  plan                  Plan
  status                SubStatus
  startDate             DateTime
  endDate               DateTime?
  cancelAtPeriodEnd     Boolean   @default(false)
  stripeSubscriptionId  String?
  stripeCustomerId      String?
  createdAt             DateTime  @default(now())
}

model ChatMessage {
  id        String   @id @default(uuid())
  userId    String
  content   String
  role      Role     // user, assistant, system
  actions   Json?
  createdAt DateTime @default(now())
}

model AIActionLog {
  id         String   @id @default(uuid())
  userId     String
  messageId  String?
  actionType String
  actionData Json
  status     String
  createdAt  DateTime @default(now())
}

enum Tier {
  FREE
  PREMIUM_MONTHLY
  PREMIUM_YEARLY
}

enum Priority {
  LOW
  MEDIUM
  HIGH
}

enum Status {
  NOT_STARTED
  IN_PROGRESS
  COMPLETED
  ABANDONED
}

enum Plan {
  FREE
  PREMIUM_MONTHLY
  PREMIUM_YEARLY
}

enum SubStatus {
  ACTIVE
  PAST_DUE
  CANCELED
  EXPIRED
}

enum Role {
  USER
  ASSISTANT
  SYSTEM
}
```

### Authentication & Security
- JWT token authentication
- OAuth integration (Google)
- Password hashing (bcrypt)
- Rate limiting
- CORS configuration
- HTTPS enforcement
- Input validation
- SQL injection prevention
- XSS protection

### Real-time Communication
- WebSocket server setup
- Connection management
- Event broadcasting
- User-specific channels
- Reconnection handling
- Heartbeat mechanism

### Background Workers
- **Reminder dispatcher**: Send scheduled reminders
- **Schedule generator**: AI schedule creation
- **Analytics aggregator**: Calculate daily metrics
- **Embedding indexer**: Update AI search index
- **Email sender**: Notifications and reports
- **Subscription checker**: Monitor subscription status

### AI Integration Services

#### LLM Service
- OpenAI/Anthropic API integration
- Prompt templates
- Intent detection
- Function calling
- Response streaming
- Token usage tracking
- Error handling and fallbacks

#### Voice Service
- **Speech-to-text**: Whisper API
- **Text-to-speech**: OpenAI TTS or similar
- Audio file processing
- Audio format conversion
- Streaming support

#### Embeddings Service
- Document embedding
- Semantic search
- Vector storage (pgvector or Pinecone)
- Similarity matching

### Caching Strategy
- Redis for:
  - Session storage
  - API response caching
  - Rate limiting counters
  - WebSocket state
  - Background job queue

### API Documentation
- OpenAPI/Swagger spec
- Interactive API docs
- Request/response examples
- Authentication documentation
- WebSocket protocol docs

### Error Handling
- Centralized error handling
- Structured error responses
- Error logging (Sentry or similar)
- User-friendly error messages
- Retry mechanisms

### Logging & Monitoring
- Structured logging
- Request/response logging
- Performance metrics
- Error tracking
- Uptime monitoring

## Technical Requirements

### Backend Setup
- FastAPI application structure
- Dependency injection
- Middleware configuration
- Environment configuration
- Database connection pooling

### Testing Infrastructure
- Unit test framework (pytest)
- Integration tests
- API endpoint tests
- WebSocket tests
- Mock external services

### CI/CD Pipeline
- Automated testing
- Code linting (flake8, black)
- Type checking (mypy)
- Security scanning
- Automated deployment

### Deployment
- Docker containerization
- Environment variables management
- Database migration automation
- Health check endpoints
- Graceful shutdown

## Acceptance Criteria

### API
- [ ] FastAPI server runs successfully
- [ ] All REST endpoints work correctly
- [ ] API documentation is accessible
- [ ] Authentication endpoints work
- [ ] JWT tokens are generated and validated
- [ ] Rate limiting is enforced
- [ ] CORS is configured properly
- [ ] Input validation works
- [ ] Error responses are consistent

### Database
- [ ] PostgreSQL connection works
- [ ] Prisma schema is defined
- [ ] Migrations run successfully
- [ ] All models are accessible via Prisma
- [ ] Database queries are optimized
- [ ] Indexes are created for performance

### WebSocket
- [ ] WebSocket server runs
- [ ] Clients can connect
- [ ] Messages broadcast correctly
- [ ] User-specific channels work
- [ ] Reconnection handling works
- [ ] Heartbeat mechanism works

### Background Workers
- [ ] Celery/RQ setup works
- [ ] Workers process jobs
- [ ] Job retry logic works
- [ ] Failed jobs are logged
- [ ] Scheduled jobs run on time

### AI Integration
- [ ] LLM API calls work
- [ ] Intent detection functions
- [ ] Speech-to-text works
- [ ] Text-to-speech works
- [ ] Token usage is tracked

### Caching
- [ ] Redis connection works
- [ ] Cache reads/writes work
- [ ] Cache invalidation works
- [ ] Session storage works

### Testing
- [ ] Unit tests pass
- [ ] Integration tests pass
- [ ] API tests pass
- [ ] Code coverage > 80%

### Deployment
- [ ] Docker builds successfully
- [ ] Migrations run on deploy
- [ ] Health checks return 200
- [ ] Environment variables work
- [ ] Logging works in production

## API Endpoint Structure

```
/api/auth/*          - Authentication
/api/users/*         - User management
/api/courses/*       - Courses
/api/goals/*         - Goals
/api/schedule/*      - Scheduling
/api/notes/*         - Notes
/api/resources/*     - Resources
/api/chat/*          - AI chat
/api/subscription/*  - Subscriptions
/api/analytics/*     - Analytics
/api/admin/*         - Admin
/ws/chat             - Chat WebSocket
/ws/updates          - Real-time updates
```

## Dependencies

- fastapi
- prisma
- psycopg2 (PostgreSQL)
- redis
- celery or rq
- python-jose (JWT)
- passlib (password hashing)
- openai or anthropic
- stripe
- websockets
- pydantic
- uvicorn

## Performance Requirements

- API response time < 200ms (90th percentile)
- WebSocket latency < 100ms
- Support 1000+ concurrent connections
- Database query time < 100ms
- Background job processing < 5 seconds

## Security Considerations

- All passwords hashed with bcrypt
- JWT tokens with expiration
- Rate limiting on all endpoints
- Input sanitization
- SQL injection prevention
- XSS protection
- CSRF protection (for cookies)
- Secure headers (HSTS, etc.)
- API key rotation
- Secrets management

## Monitoring & Observability

- Application metrics (Prometheus)
- Distributed tracing (Jaeger)
- Error tracking (Sentry)
- Uptime monitoring
- Performance monitoring (APM)
- Log aggregation

## Testing Requirements

- Unit tests for business logic
- Integration tests for endpoints
- WebSocket tests
- Background worker tests
- AI integration tests (mocked)
- Load testing
- Security testing

## Documentation Requirements

- API documentation (Swagger/OpenAPI)
- Architecture documentation
- Deployment guide
- Development setup guide
- Contributing guidelines

## Estimated Effort
Large - 5-6 sprints

## Related Issues
- All feature modules depend on backend
- Authentication & Onboarding
- AI Chat system
- Real-time updates
- Subscription billing
