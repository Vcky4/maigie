# Quick Testing Guide - Structured Logging & Error Tracking

## Prerequisites

1. **Install Dependencies**
   ```bash
   cd apps/backend
   poetry install
   ```

2. **Configure Environment** (Optional)
   Create a `.env` file in `apps/backend`:
   ```bash
   # Required
   ENVIRONMENT=development
   LOG_LEVEL=DEBUG
   
   # Optional - Enable Sentry error tracking
   SENTRY_DSN=https://your-sentry-dsn@sentry.io/project-id
   SENTRY_TRACES_SAMPLE_RATE=0.1
   ```

## Option 1: Verification Script (Recommended)

Run the standalone verification script to test logging without starting the server:

```bash
cd apps/backend
poetry run python verify_logging.py
```

**What to Expect:**
- ✅ JSON-formatted log output
- ✅ Multiple test scenarios (INFO, WARNING, ERROR logs)
- ✅ Exception logging with tracebacks
- ✅ Structured context with custom fields

## Option 2: Live API Testing

### 1. Start the Server

```bash
cd apps/backend
poetry run uvicorn src.main:app --reload
```

**Expected Startup Logs** (JSON format):
```json
{"timestamp": "2024-01-15T10:30:00", "level": "INFO", "message": "Structured logging configured", ...}
{"timestamp": "2024-01-15T10:30:01", "level": "INFO", "message": "Sentry error tracking initialized", ...}
{"timestamp": "2024-01-15T10:30:02", "level": "INFO", "message": "Starting Maigie v0.1.0", ...}
```

### 2. Test Structured Logging

```bash
curl -X POST http://localhost:8000/api/v1/examples/test/structured-logging
```

**Expected:**
- ✅ Multiple JSON log entries in console
- ✅ Different log levels (DEBUG, INFO, WARNING)
- ✅ Custom fields in `extra` parameter
- ✅ 200 OK response

### 3. Test InternalServerError (500)

```bash
curl -X POST http://localhost:8000/api/v1/examples/test/error-500
```

**Expected:**
- ✅ JSON log entry at ERROR level
- ✅ Full traceback in log output
- ✅ Error sent to Sentry (if configured)
- ✅ Generic 500 response to client:
```json
{
  "status_code": 500,
  "code": "INTERNAL_SERVER_ERROR",
  "message": "An internal server error occurred. Please try again later."
}
```

**Console Log Example:**
```json
{
  "timestamp": "2024-01-15T10:35:23",
  "level": "ERROR",
  "logger": "src.main",
  "module": "main",
  "message": "MaigieError [500-level]: INTERNAL_SERVER_ERROR - This is a test error",
  "error_code": "INTERNAL_SERVER_ERROR",
  "status_code": 500,
  "detail": "Simulated database connection failure",
  "path": "/api/v1/examples/test/error-500",
  "method": "POST",
  "traceback": "Traceback (most recent call last):\n  File ...",
  "exception": "InternalServerError: This is a test error\n..."
}
```

### 4. Test Unhandled Exception

```bash
curl -X POST http://localhost:8000/api/v1/examples/test/unhandled-exception
```

**Expected:**
- ✅ JSON log entry with exception details
- ✅ Full traceback (ZeroDivisionError)
- ✅ Error sent to Sentry (if configured)
- ✅ Generic 500 response to client

### 5. Test 404 Error (ResourceNotFoundError)

```bash
curl http://localhost:8000/api/v1/examples/ai/process/nonexistent
```

**Expected:**
- ✅ JSON log entry at WARNING level
- ✅ **NOT** sent to Sentry (4xx errors are client errors)
- ✅ Detailed 404 response:
```json
{
  "status_code": 404,
  "code": "RESOURCE_NOT_FOUND",
  "message": "Course with ID 'nonexistent' not found"
}
```

### 6. Test 403 Error (SubscriptionLimitError)

```bash
curl -X POST http://localhost:8000/api/v1/examples/ai/voice-session \
  -H "Content-Type: application/json" \
  -H "X-User-Subscription: basic" \
  -d '{"session_type": "conversation"}'
```

**Expected:**
- ✅ JSON log entry at WARNING level
- ✅ **NOT** sent to Sentry
- ✅ Detailed 403 response

## Verify Sentry Integration

### 1. Get a Sentry DSN

If you don't have one:
1. Sign up at [sentry.io](https://sentry.io)
2. Create a new project (Python/FastAPI)
3. Copy your DSN (looks like: `https://xxx@xxx.ingest.sentry.io/xxx`)

### 2. Set Environment Variable

```bash
export SENTRY_DSN="your-dsn-here"
```

### 3. Restart Server and Trigger Error

```bash
# Restart with Sentry enabled
poetry run uvicorn src.main:app --reload

# Trigger a 500 error
curl -X POST http://localhost:8000/api/v1/examples/test/error-500
```

### 4. Check Sentry Dashboard

1. Go to your Sentry project dashboard
2. You should see a new error event
3. Click it to see:
   - Full traceback
   - Request details
   - Environment info
   - Custom tags and context

## Verification Checklist

### JSON Logging ✅
- [ ] All logs are in JSON format
- [ ] Logs include timestamp, level, logger, module, function, line
- [ ] Custom fields appear in `extra` parameter
- [ ] Log level respects ENVIRONMENT variable (DEBUG in dev, INFO in prod)

### Error Handling ✅
- [ ] 500-level errors logged at ERROR level with full traceback
- [ ] 4xx errors logged at WARNING level (no traceback needed)
- [ ] Unhandled exceptions logged with full context
- [ ] Client receives sanitized error responses (no internal details)

### Sentry Integration ✅
- [ ] Sentry initializes on startup (if DSN provided)
- [ ] 500-level errors appear in Sentry dashboard
- [ ] Unhandled exceptions appear in Sentry dashboard
- [ ] 4xx errors do NOT appear in Sentry (correct behavior)
- [ ] Full traceback visible in Sentry event details

## Troubleshooting

### Logs Not in JSON Format
- **Check:** Ensure `configure_logging()` is called before any logging
- **Check:** No other code is reconfiguring the root logger
- **Fix:** Restart the server

### Sentry Not Receiving Errors
- **Check:** `SENTRY_DSN` environment variable is set
- **Check:** Startup logs show "Sentry error tracking initialized"
- **Check:** Error is 500-level (4xx errors aren't sent)
- **Check:** Sentry project is active and not rate-limited

### Import Errors
```bash
# Reinstall dependencies
poetry install

# Verify python-json-logger and sentry-sdk are installed
poetry show | grep -E "python-json-logger|sentry-sdk"
```

## Success Criteria

Your implementation is successful if:

✅ **Logging output is in JSON format** when running locally  
✅ **InternalServerError (500) generates:**
   - Structured log entry with full traceback
   - Event in Sentry (if configured)
   - Generic 500 response to client

✅ **All tests pass** without exposing internal details to clients

## Next Steps

1. **Production Deployment:**
   - Set `ENVIRONMENT=production`
   - Set `LOG_LEVEL=INFO` (or WARNING)
   - Configure `SENTRY_DSN` for error tracking

2. **Log Aggregation:**
   - Connect logs to ELK, Datadog, or CloudWatch
   - Set up dashboards and alerts
   - Monitor error rates and patterns

3. **Sentry Configuration:**
   - Set up alerts for high error rates
   - Configure integrations (Slack, email, etc.)
   - Enable performance monitoring (adjust traces_sample_rate)

For more details, see `LOGGING_AND_ERROR_TRACKING.md`.

