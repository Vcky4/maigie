# Logging and Error Tracking Guide

## Overview

The Maigie backend application uses structured JSON logging and integrates with Sentry for error tracking. This provides comprehensive observability and debugging capabilities for production environments.

## Features

### Structured (JSON) Logging
- **Format**: All logs are output in JSON format for easy parsing and aggregation
- **Fields**: Each log entry includes:
  - `timestamp`: ISO 8601 formatted timestamp
  - `level`: Log level (DEBUG, INFO, WARNING, ERROR, CRITICAL)
  - `logger`: Logger name (typically module name)
  - `module`: Module name where log originated
  - `function`: Function name where log originated
  - `line`: Line number
  - `message`: The log message
  - `environment`: Application environment
  - `application`: Always "maigie-backend"
  - Additional custom fields via `extra` parameter

### Error Tracking with Sentry
- **Automatic Capture**: All 500-level errors and unhandled exceptions are automatically sent to Sentry
- **Context**: Includes request details, user agent, traceback, and custom metadata
- **Performance**: Optional transaction tracing for performance monitoring
- **Integration**: Seamlessly integrated with FastAPI and Python logging

## Environment Variables

### Required for Full Functionality

```bash
# Logging Configuration
LOG_LEVEL=INFO                    # Log level: DEBUG, INFO, WARNING, ERROR, CRITICAL
ENVIRONMENT=production            # Environment: development, staging, production

# Sentry Error Tracking (Optional but Recommended)
SENTRY_DSN=https://xxx@sentry.io/xxx  # Sentry project DSN
SENTRY_TRACES_SAMPLE_RATE=0.1    # Performance tracing sample rate (0.0-1.0)
```

### Default Behavior

- **LOG_LEVEL**: Defaults to `DEBUG` in development, `INFO` in production
- **ENVIRONMENT**: Defaults to `development`
- **SENTRY_DSN**: If not set, error tracking is disabled (warning logged)

## Usage Examples

### Basic Logging

```python
import logging

logger = logging.getLogger(__name__)

# Simple log message
logger.info("User logged in successfully")

# Log with additional context
logger.info(
    "User login successful",
    extra={
        "user_id": "123",
        "ip_address": "192.168.1.1",
        "login_method": "oauth"
    }
)

# Error logging with exception
try:
    result = risky_operation()
except Exception as e:
    logger.error(
        "Operation failed",
        exc_info=True,  # Include traceback
        extra={
            "operation": "risky_operation",
            "user_id": user_id
        }
    )
```

### Exception Handling

The application automatically logs and tracks exceptions through global exception handlers:

#### MaigieError Exceptions
```python
from src.utils.exceptions import InternalServerError, ResourceNotFoundError

# 4xx errors - logged at WARNING level
raise ResourceNotFoundError("Course", "course-123")

# 500 errors - logged at ERROR level with full traceback + sent to Sentry
raise InternalServerError(
    message="Database connection failed",
    detail="Connection pool exhausted"
)
```

#### Unhandled Exceptions
All unhandled exceptions are:
1. Logged at ERROR level with full traceback and context
2. Sent to Sentry with all available metadata
3. Returned to client as generic 500 error (no details leaked)

## Log Output Example

### JSON Log Entry (Production)
```json
{
  "timestamp": "2024-01-15T10:30:45",
  "level": "ERROR",
  "logger": "src.routes.courses",
  "module": "courses",
  "function": "get_course",
  "line": 42,
  "message": "MaigieError [500-level]: INTERNAL_SERVER_ERROR - Database query failed",
  "environment": "production",
  "application": "maigie-backend",
  "error_code": "INTERNAL_SERVER_ERROR",
  "status_code": 500,
  "detail": "Connection timeout after 30s",
  "path": "/api/v1/courses/123",
  "method": "GET",
  "user_agent": "Mozilla/5.0...",
  "traceback": "Traceback (most recent call last):\\n..."
}
```

## Testing Locally

### 1. Install Dependencies
```bash
cd apps/backend
poetry install
```

### 2. Set Environment Variables
Create a `.env` file:
```bash
ENVIRONMENT=development
LOG_LEVEL=DEBUG
SENTRY_DSN=https://your-sentry-dsn@sentry.io/project-id  # Optional
```

### 3. Run the Application
```bash
poetry run uvicorn src.main:app --reload
```

### 4. Test Error Logging

#### Test 500 Error (InternalServerError)
```bash
# Create a test endpoint or use Python:
curl -X POST http://localhost:8000/api/v1/test/error-500
```

Or using Python:
```python
# In any route handler:
from src.utils.exceptions import InternalServerError

@router.get("/test/error-500")
async def test_error():
    raise InternalServerError(
        message="This is a test error",
        detail="Testing structured logging and Sentry"
    )
```

**Expected Result**:
- Console shows JSON log with full traceback
- Sentry dashboard shows the error (if configured)
- Client receives generic 500 error response

#### Test 404 Error (ResourceNotFoundError)
```python
from src.utils.exceptions import ResourceNotFoundError

@router.get("/test/error-404")
async def test_not_found():
    raise ResourceNotFoundError("TestResource", "test-123")
```

**Expected Result**:
- Console shows JSON log at WARNING level
- No Sentry event (4xx errors are not sent to Sentry)
- Client receives detailed 404 error response

## Integration with Existing Code

### The logging system is automatically configured during application startup:

1. **Initialization** (in `src/main.py`):
   - `configure_logging()` is called first
   - Sentry is initialized if DSN is provided
   - All subsequent logs use JSON format

2. **Exception Handlers** (in `src/main.py`):
   - `maigie_error_handler`: Handles all MaigieError exceptions
   - `unhandled_exception_handler`: Catches all other exceptions
   - Both log with full context before returning responses

3. **Custom Logging** (anywhere in the app):
   ```python
   import logging
   logger = logging.getLogger(__name__)
   
   # Use logger as normal - JSON formatting is automatic
   logger.info("Processing request", extra={"user_id": user_id})
   ```

## Best Practices

### 1. Use Structured Context
```python
# Good - structured data in extra
logger.info("User action", extra={"user_id": "123", "action": "login"})

# Bad - string interpolation
logger.info(f"User 123 performed login")
```

### 2. Include Relevant Context
- User IDs (when applicable)
- Request paths and methods
- Resource IDs
- Operation names
- Error details

### 3. Choose Appropriate Log Levels
- **DEBUG**: Detailed debugging information (dev only)
- **INFO**: General informational messages (successful operations)
- **WARNING**: Something unexpected but handled (4xx errors)
- **ERROR**: Errors that need attention (500 errors, failures)
- **CRITICAL**: System-critical failures

### 4. Use exc_info for Exceptions
```python
try:
    risky_operation()
except Exception as e:
    logger.error("Operation failed", exc_info=True)  # Includes traceback
```

## Monitoring and Alerts

### Sentry Features
- **Real-time Alerts**: Configure alerts for error spikes or specific errors
- **Performance Monitoring**: Track slow endpoints and database queries
- **Release Tracking**: Associate errors with specific releases
- **User Context**: Track which users are affected by errors

### Log Aggregation
JSON logs can be easily aggregated and searched using:
- **ELK Stack** (Elasticsearch, Logstash, Kibana)
- **Datadog**
- **CloudWatch Logs** (AWS)
- **Stackdriver** (GCP)

## Troubleshooting

### Logs Not Appearing in JSON Format
- Ensure `configure_logging()` is called during startup
- Check `LOG_LEVEL` environment variable
- Verify no other code is reconfiguring the logger

### Errors Not Appearing in Sentry
- Verify `SENTRY_DSN` is set correctly
- Check Sentry initialization logs during startup
- Ensure the error is 500-level or unhandled
- Check Sentry dashboard for rate limits or quota issues

### Missing Context in Logs
- Use `extra` parameter to add custom fields
- Ensure exception handlers are being triggered
- Check that middleware is properly installed

## Additional Resources

- [Python JSON Logger Documentation](https://github.com/madzak/python-json-logger)
- [Sentry Python SDK Documentation](https://docs.sentry.io/platforms/python/)
- [FastAPI Logging Best Practices](https://fastapi.tiangolo.com/tutorial/logging/)

