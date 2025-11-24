# Backend (FastAPI)

FastAPI backend application for Maigie.

## Setup

### Quick Setup (Windows PowerShell)

Run the automated setup script:
```powershell
cd apps/backend
.\setup-dev.ps1
```

This will:
- Check Python installation
- Install Poetry if needed
- Configure Poetry to create virtual environment in project directory (`.venv`)
- Install all dependencies (creates virtual environment automatically)
- Create `.env` file from `.env.example`
- Verify the setup

### Manual Setup

1. **Install Poetry** (if not already installed):
   ```powershell
   # Windows PowerShell
   .\install-poetry.ps1
   
   # Or use the official installer
   (Invoke-WebRequest -Uri https://install.python-poetry.org -UseBasicParsing).Content | python -
   ```

2. **Configure Poetry for in-project virtual environment** (recommended):
   ```bash
   poetry config virtualenvs.in-project true
   ```

3. **Install dependencies:**
   ```bash
   poetry install
   ```
   
   This will automatically create a `.venv` directory in your project with all dependencies.

4. **Copy environment variables** (optional, defaults are used if not set):
   ```powershell
   # Windows PowerShell
   Copy-Item .env.example .env
   
   # Linux/Mac
   cp .env.example .env
   ```
   
   Edit `.env` and set your `DATABASE_URL` (e.g., `postgresql://postgres:postgres@localhost:5432/maigie`)

5. **Generate Prisma Client:**
   ```bash
   poetry run prisma generate
   ```

6. **Run database migrations:**
   ```bash
   # Create initial migration
   poetry run prisma migrate dev --name init
   ```

7. **Run the development server:**
   ```bash
   nx serve backend
   ```

   Or directly:
   ```bash
   cd apps/backend
   poetry run uvicorn src.main:app --reload
   ```

## Verify Application Setup

To verify that the Application Setup requirements are met:

```bash
# Using Poetry
cd apps/backend
poetry run python verify_setup.py

# Or using system Python (if dependencies are installed)
python verify_setup.py
```

To verify the Authentication Framework:

```bash
# Using Poetry
cd apps/backend
poetry run python verify_auth.py

# Or using system Python (if dependencies are installed)
python verify_auth.py
```

Or run the tests:
```bash
nx test backend
# or
poetry run pytest
```

## Project Structure

### Core Application Files
- `src/main.py` - FastAPI app factory and application entry point
- `src/config.py` - Environment configuration management
- `src/dependencies.py` - Dependency injection system
- `src/middleware.py` - Custom middleware (logging, security headers)
- `src/exceptions.py` - Custom exception classes and handlers

### Core Utilities (`src/core/`)
- `core/security.py` - JWT utilities, password hashing (bcrypt)
- `core/oauth.py` - OAuth provider base structure (Google, GitHub)
- `core/websocket.py` - WebSocket connection manager and event broadcasting
- `core/database.py` - Database connection manager (legacy placeholder)
- `core/cache.py` - Cache connection manager (legacy placeholder)

### Database & Dependencies (`src/utils/`)
- `utils/dependencies.py` - Dependency injection for Prisma and Redis clients
  - `get_db_client()` - FastAPI dependency for Prisma database client
  - `get_redis_client()` - FastAPI dependency for Redis cache client
  - Lifecycle management functions for startup/shutdown

### Database Schema (`prisma/`)
- `prisma/schema.prisma` - Prisma schema with database models (User, Course, Goal)

### Feature Modules
- `src/routes/` - API route handlers
  - `routes/auth.py` - Authentication routes (login, register, OAuth)
  - `routes/realtime.py` - WebSocket routes for real-time updates
- `src/services/` - Business logic
- `src/models/` - Pydantic schemas and ORM models
  - `models/auth.py` - Authentication models (UserRegister, UserLogin, TokenResponse, etc.)
  - `models/websocket.py` - WebSocket message models
- `src/db/` - Database connection and migrations
- `src/tasks/` - Background tasks (Celery/Dramatiq)
- `src/ai_client/` - LLM and embeddings clients
- `src/workers/` - Async workers
- `src/utils/` - Utility functions

### Tests
- `tests/` - Test files
- `verify_setup.py` - Application setup verification script
- `verify_auth.py` - Authentication framework verification script
- `verify_websocket.py` - WebSocket framework verification script (run `.venv\Scripts\python.exe verify_websocket.py`)

## Application Setup Status

✅ **FastAPI server runs successfully** - Application factory pattern implemented  
✅ **Application structure follows defined patterns** - Organized according to Backend Infrastructure issue  
✅ **Environment configuration works correctly** - Pydantic Settings with .env support  
✅ **Dependency injection system works** - FastAPI Depends pattern implemented  
✅ **Middleware stack is configured** - Logging, Security Headers, and CORS middleware

## WebSocket Framework Status

✅ **WebSocket server runs** - Endpoint available at `/api/v1/realtime/ws`  
✅ **Connection manager works** - Tracks connections, users, and channels  
✅ **Event broadcasting framework works** - Broadcast to all/users/channels  
✅ **Reconnection handling utilities work** - Tracks reconnection attempts  
✅ **Heartbeat mechanism works** - Automatic ping/pong with timeout detection

### Testing WebSocket Framework

Run the verification script:

```bash
# Using virtual environment
.venv\Scripts\python.exe verify_websocket.py

# Or using Poetry
poetry run python verify_websocket.py

# Or using PowerShell script
powershell -ExecutionPolicy Bypass -File scripts\verify-websocket.ps1
```

## Authentication Framework Status

✅ **JWT utilities are available** - Access and refresh token creation/decoding  
✅ **Password hashing utilities work** - bcrypt-based password hashing and verification  
✅ **OAuth base structure is in place** - Google and GitHub OAuth providers  
✅ **Security middleware is configured** - Security headers and request logging

## API Endpoints

### Core Endpoints
- `GET /` - Root endpoint with app info
- `GET /health` - Multi-service health check (validates DB and cache connectivity)
- `GET /ready` - Readiness check (includes DB and cache status)
- `GET /docs` - Interactive API documentation (Swagger UI)
- `GET /redoc` - Alternative API documentation (ReDoc)
- `GET /openapi.json` - OpenAPI schema

### Authentication Endpoints
- `POST /api/v1/auth/register` - Register a new user
- `POST /api/v1/auth/login` - Login with email/password
- `POST /api/v1/auth/refresh` - Refresh access token
- `GET /api/v1/auth/me` - Get current authenticated user (requires Bearer token)
- `GET /api/v1/auth/oauth/{provider}/authorize` - Initiate OAuth flow (google, github)
- `GET /api/v1/auth/oauth/{provider}/callback` - OAuth callback endpoint
- `GET /api/v1/auth/oauth/providers` - List available OAuth providers

### API Module Endpoints
All module endpoints are registered with `/api/v1` prefix:
- `/api/v1/ai` - AI assistant routes
- `/api/v1/courses` - Course management routes
- `/api/v1/goals` - Goal tracking routes
- `/api/v1/schedule` - Schedule management routes
- `/api/v1/resources` - Resource management routes
- `/api/v1/realtime` - WebSocket real-time communication

## Virtual Environment

Poetry automatically creates and manages a virtual environment for this project. The setup script configures Poetry to create the virtual environment in the project directory (`.venv`).

**Using the virtual environment:**
- All commands should be run with `poetry run` prefix:
  ```bash
  poetry run python verify_setup.py
  poetry run uvicorn src.main:app --reload
  poetry run pytest
  ```

- Or activate the virtual environment manually:
  ```powershell
  # Windows PowerShell
  .venv\Scripts\Activate.ps1
  
  # Then run commands directly
  python verify_setup.py
  uvicorn src.main:app --reload
  ```

**Note:** The `.venv` directory is gitignored and should not be committed.

## Environment Variables

See `.env.example` for available configuration options. Key settings:

- `APP_NAME` - Application name
- `DEBUG` - Enable debug mode
- `ENVIRONMENT` - Environment (development/production)
- `SECRET_KEY` - Secret key for JWT tokens
- `ALGORITHM` - JWT algorithm (default: HS256)
- `ACCESS_TOKEN_EXPIRE_MINUTES` - Access token expiration time
- `REFRESH_TOKEN_EXPIRE_DAYS` - Refresh token expiration time
- `CORS_ORIGINS` - Allowed CORS origins
- `OAUTH_GOOGLE_CLIENT_ID` - Google OAuth client ID (optional)
- `OAUTH_GOOGLE_CLIENT_SECRET` - Google OAuth client secret (optional)
- `OAUTH_GITHUB_CLIENT_ID` - GitHub OAuth client ID (optional)
- `OAUTH_GITHUB_CLIENT_SECRET` - GitHub OAuth client secret (optional)
- `OAUTH_REDIRECT_URI` - OAuth redirect URI
- `DATABASE_URL` - PostgreSQL connection string (required for Prisma)
- `REDIS_URL` - Redis connection string (not yet implemented)

## Database Setup

This project uses Prisma as the ORM for PostgreSQL.

### Prerequisites
- PostgreSQL installed and running
- Database created (e.g., `maigie`)

### Setup Steps

1. **Configure database URL in `.env`:**
   ```env
   DATABASE_URL="postgresql://username:password@localhost:5432/maigie"
   ```

2. **Generate Prisma Client:**
   ```bash
   poetry run prisma generate
   ```

3. **Run migrations:**
   ```bash
   # Create and apply migration
   poetry run prisma migrate dev --name init
   ```

4. **Optional: Open Prisma Studio (database GUI):**
   ```bash
   poetry run prisma studio
   ```

### Prisma Commands

- `prisma generate` - Generate Prisma Client
- `prisma migrate dev` - Create and apply migrations in development
- `prisma migrate deploy` - Apply migrations in production
- `prisma studio` - Open Prisma Studio (database GUI)
- `prisma db push` - Push schema changes without migrations (dev only)
- `prisma db seed` - Run database seeds

### Database Models

Current schema includes:
- **User** - User accounts
- **Course** - Courses
- **Goal** - User goals

See `prisma/schema.prisma` for the complete schema definition.

