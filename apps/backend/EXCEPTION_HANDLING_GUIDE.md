# Exception Handling Guide

## Overview

The Maigie backend implements a comprehensive, standardized exception handling system that ensures consistent error responses across all API endpoints while preventing the exposure of internal implementation details.

## Architecture

### 1. Standardized Error Response Model

All errors follow the `ErrorResponse` Pydantic model:

```python
{
    "status_code": int,      # HTTP status code
    "code": str,             # Application-specific error code
    "message": str,          # User-friendly message
    "detail": str | None     # Internal details (debug only)
}
```

**Location:** `src/models/error_response.py`

### 2. Custom Exception Classes

All custom exceptions inherit from `MaigieError` base class:

**Location:** `src/utils/exceptions.py`

#### Available Exception Classes

| Exception | Status Code | Error Code | Use Case |
|-----------|-------------|------------|----------|
| `SubscriptionLimitError` | 403 | `SUBSCRIPTION_LIMIT_EXCEEDED` | Basic users accessing Premium features |
| `ResourceNotFoundError` | 404 | `RESOURCE_NOT_FOUND` | Requested resource doesn't exist |
| `UnauthorizedError` | 401 | `UNAUTHORIZED` | Authentication required |
| `ForbiddenError` | 403 | `FORBIDDEN` | Insufficient permissions |
| `ValidationError` | 422 | `VALIDATION_ERROR` | Business logic validation failures |
| `InternalServerError` | 500 | `INTERNAL_SERVER_ERROR` | Unexpected errors |

### 3. Global Exception Handlers

Three global handlers are registered in `main.py`:

1. **`maigie_error_handler`** - Handles all `MaigieError` exceptions
2. **`validation_error_handler`** - Handles Pydantic `RequestValidationError`
3. **`unhandled_exception_handler`** - Catches all unhandled exceptions

## Usage Examples

### Example 1: Subscription Limit

```python
from ..utils.exceptions import SubscriptionLimitError

@router.post("/voice-session")
async def start_voice_session(user_subscription: str):
    if user_subscription != "premium":
        raise SubscriptionLimitError(
            message="Voice AI sessions require a Premium subscription",
            detail=f"User has {user_subscription} subscription"
        )
    
    # Proceed with voice session
    return {"status": "session_started"}
```

**Client receives:**
```json
{
    "status_code": 403,
    "code": "SUBSCRIPTION_LIMIT_EXCEEDED",
    "message": "Voice AI sessions require a Premium subscription"
}
```

### Example 2: Resource Not Found

```python
from ..utils.exceptions import ResourceNotFoundError

@router.get("/courses/{course_id}")
async def get_course(course_id: str):
    course = await db.course.find_unique(where={"id": course_id})
    
    if not course:
        raise ResourceNotFoundError(
            resource_type="Course",
            resource_id=course_id,
            detail="Course lookup failed in database"
        )
    
    return course
```

**Client receives:**
```json
{
    "status_code": 404,
    "code": "RESOURCE_NOT_FOUND",
    "message": "Course with ID 'abc123' not found"
}
```

### Example 3: Request Validation Error

When a client sends invalid data:

```python
# Client sends invalid request
POST /api/v1/ai/chat
{
    "mesage": "Hello"  # Typo: should be "message"
}
```

**Client receives:**
```json
{
    "status_code": 400,
    "code": "VALIDATION_ERROR",
    "message": "Validation error in field 'body -> message': field required"
}
```

### Example 4: Unhandled Exception (Safety Net)

```python
@router.get("/process")
async def process_data():
    # Something unexpected happens
    result = 1 / 0  # ZeroDivisionError
    return result
```

**Server logs full traceback**
**Client receives (no internal details leaked):**
```json
{
    "status_code": 500,
    "code": "INTERNAL_SERVER_ERROR",
    "message": "An internal server error occurred. Please try again later."
}
```

## Testing the Exception System

### Using cURL

**Test Subscription Limit:**
```bash
curl -X POST http://localhost:8000/api/v1/examples/ai/voice-session \
  -H "Content-Type: application/json" \
  -H "X-User-Subscription: basic" \
  -d '{"session_type": "conversation"}'
```

**Test Resource Not Found:**
```bash
curl http://localhost:8000/api/v1/examples/ai/process/nonexistent
```

**Get Example Endpoints Info:**
```bash
curl http://localhost:8000/api/v1/examples/info
```

**Test Validation Error:**
```bash
curl -X POST http://localhost:8000/api/v1/ai/chat \
  -H "Content-Type: application/json" \
  -d '{}'
```

### Using Swagger UI

1. Navigate to `http://localhost:8000/docs`
2. Look for the "examples" and "exception-handling" tags
3. Try the example endpoints with different inputs:
   - `/api/v1/examples/ai/voice-session` without Premium header
   - `/api/v1/examples/ai/process/{course_id}` with `nonexistent`
   - `/api/v1/examples/info` to see all available examples

### Using Automated Tests

Run the test suite:
```bash
cd apps/backend
poetry run pytest tests/test_exception_handling.py -v
```

## Best Practices

### 1. Use Specific Exceptions

```python
# ✅ Good - Specific exception
raise SubscriptionLimitError(
    message="Voice AI requires Premium subscription"
)

# ❌ Bad - Generic exception
raise Exception("User can't access this")
```

### 2. Include Helpful Details

```python
# ✅ Good - Helpful detail for debugging
raise ResourceNotFoundError(
    resource_type="Course",
    resource_id=course_id,
    detail=f"Database query returned None for course_id={course_id}"
)

# ❌ Bad - No context
raise ResourceNotFoundError("Course", course_id)
```

### 3. User-Friendly Messages

```python
# ✅ Good - Clear, actionable message
"This feature requires a Premium subscription. Upgrade to continue."

# ❌ Bad - Technical jargon
"Subscription tier validation failed: user.tier != 'premium'"
```

### 4. Never Leak Sensitive Information

```python
# ✅ Good - Safe error message
raise InternalServerError(
    message="Database connection failed",
    detail="Connection to postgres://localhost:5432 timed out"
)

# ❌ Bad - Leaks credentials (never do this!)
raise InternalServerError(
    message=f"Failed to connect to postgres://user:password@host:5432"
)
```

## Security Considerations

1. **Debug Mode:** The `detail` field is only included when `DEBUG=true` in settings
2. **Logging:** Full tracebacks are logged server-side but never sent to clients
3. **Generic Errors:** Unhandled exceptions always return generic 500 errors
4. **No Stack Traces:** Stack traces are never exposed in API responses

## Error Code Reference

All application error codes follow the pattern: `[DOMAIN]_[SPECIFIC_ERROR]`

| Code | Description |
|------|-------------|
| `SUBSCRIPTION_LIMIT_EXCEEDED` | User's subscription tier doesn't allow this feature |
| `RESOURCE_NOT_FOUND` | Requested database resource doesn't exist |
| `VALIDATION_ERROR` | Request data validation failed |
| `UNAUTHORIZED` | Authentication is required |
| `FORBIDDEN` | User lacks necessary permissions |
| `INTERNAL_SERVER_ERROR` | Unexpected server error occurred |

## Troubleshooting

### Issue: Errors Not Following Standard Format

**Solution:** Ensure you're raising exceptions from `utils.exceptions`, not Python's built-in exceptions.

### Issue: Detail Field Showing in Production

**Solution:** Set `DEBUG=false` in your `.env` file for production environments.

### Issue: Stack Traces Visible to Clients

**Solution:** Verify that `unhandled_exception_handler` is registered in `main.py`.

## Future Enhancements

Potential improvements to the exception system:

1. **Error Tracking Integration:** Add Sentry or similar service integration
2. **Localization:** Support multiple languages for error messages
3. **Rate Limiting Errors:** Add specific exception for rate limit violations
4. **Retry Headers:** Include retry-after headers for temporary failures
5. **Correlation IDs:** Add request correlation IDs for tracing

