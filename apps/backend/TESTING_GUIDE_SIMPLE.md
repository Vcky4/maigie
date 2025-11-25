# 🧪 Complete Testing Guide - Step by Step (Super Simple!)

## 🎯 What We're Testing

We want to make sure:
1. ✅ Logs appear in **JSON format** (like a computer language)
2. ✅ When errors happen, they get **logged with full details**
3. ✅ When bad errors (500) happen, they get **sent to Sentry**
4. ✅ Users get **safe error messages** (no secrets leaked)

---

## 📋 Step 1: Make Sure Your Server is Running

### What to do:
1. Open your terminal (PowerShell or Command Prompt)
2. Go to the backend folder:
   ```powershell
   cd apps/backend
   ```
3. Start the server:
   ```powershell
   npx nx serve backend
   ```

### What you should see:
Look for these messages in your terminal:
```
INFO:     Uvicorn running on http://0.0.0.0:8000
{"timestamp": "...", "level": "INFO", "message": "Structured logging configured", ...}
{"timestamp": "...", "level": "INFO", "message": "Starting Maigie API v0.1.0", ...}
```

**✅ Good sign:** You see JSON logs (they look like `{"key": "value"}` format)

**❌ Bad sign:** If you see an error, stop and fix it first!

---

## 📋 Step 2: Test That Logging Works (Easy Test)

### What to do:
Open a **NEW terminal window** (keep the server running in the first one!)

Then run this command:
```powershell
curl -X POST http://localhost:8000/api/v1/examples/test/structured-logging
```

### What you should see:

**In your NEW terminal (where you ran curl):**
```json
{
  "message": "Structured logging test completed",
  "logs_generated": 4,
  "log_levels": ["DEBUG", "INFO", "WARNING", "INFO"],
  "note": "Check console for JSON-formatted log entries"
}
```

**In your SERVER terminal (where the server is running):**
You should see **4 new JSON log entries** that look like this:

```json
{"timestamp": "2025-11-25T15:30:00", "level": "DEBUG", "message": "Database query executed", "query": "SELECT * FROM courses...", ...}
{"timestamp": "2025-11-25T15:30:00", "level": "INFO", "message": "User action completed successfully", "user_id": "test-user-123", ...}
{"timestamp": "2025-11-25T15:30:00", "level": "WARNING", "message": "Slow operation detected", "duration_ms": 2500, ...}
{"timestamp": "2025-11-25T15:30:00", "level": "INFO", "message": "External API call completed", "api": "openai", ...}
```

### ✅ Checklist:
- [ ] I see JSON logs (they have `{` and `}` brackets)
- [ ] I see different log levels (DEBUG, INFO, WARNING)
- [ ] Each log has a timestamp, level, and message
- [ ] The curl command returned a success message

**If all checked ✅ → Move to Step 3!**

---

## 📋 Step 3: Test a 404 Error (Not Found Error)

### What to do:
In your NEW terminal, run:
```powershell
curl http://localhost:8000/api/v1/examples/ai/process/nonexistent
```

### What you should see:

**In your NEW terminal:**
```json
{
  "status_code": 404,
  "code": "RESOURCE_NOT_FOUND",
  "message": "Course with ID 'nonexistent' not found"
}
```

**In your SERVER terminal:**
You should see a **WARNING level** log (not ERROR):
```json
{"timestamp": "...", "level": "WARNING", "message": "MaigieError: RESOURCE_NOT_FOUND - Course with ID 'nonexistent' not found", "error_code": "RESOURCE_NOT_FOUND", "status_code": 404, ...}
```

### ✅ Checklist:
- [ ] I got a 404 error response (that's correct!)
- [ ] The log shows `"level": "WARNING"` (not ERROR)
- [ ] The log has the error code and status code
- [ ] **IMPORTANT:** This error should NOT appear in Sentry (4xx errors are client errors, not server errors)

**If all checked ✅ → Move to Step 4!**

---

## 📋 Step 4: Test a 500 Error (Internal Server Error) - THE BIG TEST!

This is the most important test! It checks if errors go to Sentry.

### What to do:
In your NEW terminal, run:
```powershell
curl -X POST http://localhost:8000/api/v1/examples/test/error-500
```

### What you should see:

**In your NEW terminal:**
```json
{
  "status_code": 500,
  "code": "INTERNAL_SERVER_ERROR",
  "message": "An internal server error occurred. Please try again later."
}
```
**Note:** The message is generic (safe) - no internal details leaked! ✅

**In your SERVER terminal:**
You should see an **ERROR level** log with a full traceback:
```json
{
  "timestamp": "2025-11-25T15:35:00",
  "level": "ERROR",
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

### ✅ Checklist:
- [ ] I got a 500 error response (that's correct!)
- [ ] The response message is generic (safe for users)
- [ ] The log shows `"level": "ERROR"` (not WARNING)
- [ ] The log has a `"traceback"` field with error details
- [ ] The log has `"path"` and `"method"` fields

**If all checked ✅ → Move to Step 5 (Sentry Check)!**

---

## 📋 Step 5: Check Sentry Dashboard (Error Tracking)

This step checks if errors are being sent to Sentry.

### What to do:

1. **Open your web browser**
2. **Go to your Sentry project dashboard**
   - Usually: https://sentry.io/organizations/YOUR-ORG/projects/YOUR-PROJECT/
   - Or check your email for the Sentry link

3. **Look for the error you just triggered:**
   - You should see a new error event
   - It should say something like: "InternalServerError: This is a test error"
   - The timestamp should match when you ran the curl command

4. **Click on the error** to see details:
   - You should see the **full traceback**
   - You should see **request details** (path, method)
   - You should see **environment** (development)
   - You should see **release version** (0.1.0)

### ✅ Checklist:
- [ ] I can see the error in Sentry dashboard
- [ ] The error has a traceback (the full error details)
- [ ] The error shows the path: `/api/v1/examples/test/error-500`
- [ ] The error shows the environment: `development`

**If all checked ✅ → Move to Step 6!**

**❌ If you DON'T see the error in Sentry:**
- Check that `SENTRY_DSN` is set in your `.env` file
- Check that the server restarted after adding the DSN
- Look at server logs for "Sentry error tracking initialized" message
- Make sure you're looking at the right Sentry project

---

## 📋 Step 6: Test an Unhandled Exception

This tests what happens when something completely unexpected breaks.

### What to do:
In your NEW terminal, run:
```powershell
curl -X POST http://localhost:8000/api/v1/examples/test/unhandled-exception
```

### What you should see:

**In your NEW terminal:**
```json
{
  "status_code": 500,
  "code": "INTERNAL_SERVER_ERROR",
  "message": "An internal server error occurred. Please try again later."
}
```

**In your SERVER terminal:**
You should see an **ERROR level** log:
```json
{
  "timestamp": "...",
  "level": "ERROR",
  "message": "Unhandled exception: ZeroDivisionError: division by zero",
  "exception_type": "ZeroDivisionError",
  "exception_message": "division by zero",
  "path": "/api/v1/examples/test/unhandled-exception",
  "method": "POST",
  "traceback": "Traceback (most recent call last):\n  File ...",
  ...
}
```

### ✅ Checklist:
- [ ] I got a 500 error response (generic message)
- [ ] The log shows `"level": "ERROR"`
- [ ] The log shows `"exception_type": "ZeroDivisionError"`
- [ ] The log has a full traceback
- [ ] **Check Sentry:** This error should also appear in Sentry dashboard

**If all checked ✅ → Move to Step 7!**

---

## 📋 Step 7: Verify JSON Format is Correct

Let's make sure all logs are in proper JSON format.

### What to do:

1. **Look at your server terminal**
2. **Find any log entry** (they should all be JSON)
3. **Copy one log entry** and check if it's valid JSON

### How to check if JSON is valid:

**Option A: Use an online tool**
1. Go to https://jsonlint.com/
2. Paste your log entry
3. Click "Validate JSON"
4. It should say "Valid JSON" ✅

**Option B: Look for these signs:**
- ✅ Starts with `{` and ends with `}`
- ✅ Has `"key": "value"` pairs
- ✅ Keys are in quotes: `"timestamp"`, `"level"`, etc.
- ✅ Values are in quotes (for text) or numbers (for numbers)

### ✅ Checklist:
- [ ] All logs start with `{` and end with `}`
- [ ] All logs have `"timestamp"` field
- [ ] All logs have `"level"` field
- [ ] All logs have `"message"` field
- [ ] JSON validator says it's valid (if you used the tool)

**If all checked ✅ → Move to Step 8 (Final Verification)!**

---

## 📋 Step 8: Final Verification Checklist

Let's make sure everything is working perfectly!

### ✅ Complete Checklist:

#### Logging:
- [ ] All logs appear in JSON format
- [ ] Logs have timestamp, level, message, logger, module, function, line
- [ ] Different log levels work (DEBUG, INFO, WARNING, ERROR)
- [ ] Custom fields in `extra` parameter appear in logs

#### Error Handling:
- [ ] 404 errors log at WARNING level
- [ ] 500 errors log at ERROR level with full traceback
- [ ] Unhandled exceptions log at ERROR level with full traceback
- [ ] Client responses are safe (no internal details leaked)

#### Sentry Integration:
- [ ] Sentry initializes on startup (check for "Sentry error tracking initialized" log)
- [ ] 500-level errors appear in Sentry dashboard
- [ ] Unhandled exceptions appear in Sentry dashboard
- [ ] 4xx errors do NOT appear in Sentry (correct behavior!)

#### Test Endpoints:
- [ ] `/api/v1/examples/test/structured-logging` works
- [ ] `/api/v1/examples/test/error-500` works
- [ ] `/api/v1/examples/test/unhandled-exception` works
- [ ] `/api/v1/examples/ai/process/nonexistent` returns 404

---

## 🎉 Success Criteria

You've successfully completed testing if:

1. ✅ **All logs are in JSON format** - You can see `{...}` in your terminal
2. ✅ **500 errors generate structured logs** - Full traceback in JSON logs
3. ✅ **500 errors trigger Sentry events** - Errors appear in Sentry dashboard
4. ✅ **Generic error responses** - Clients get safe messages, no secrets leaked

---

## 🐛 Troubleshooting

### Problem: Logs are NOT in JSON format
**Solution:**
- Make sure `configure_logging()` is called in `lifespan()` function
- Check that `python-json-logger` is installed: `poetry show python-json-logger`
- Restart the server

### Problem: Errors are NOT appearing in Sentry
**Solution:**
- Check `.env` file has `SENTRY_DSN=your-dsn-here`
- Restart the server after adding DSN
- Check server logs for "Sentry error tracking initialized"
- Make sure you're looking at the correct Sentry project
- Only 500-level errors go to Sentry (4xx errors don't)

### Problem: Can't see traceback in logs
**Solution:**
- Make sure you're using `exc_info=True` in logger.error() calls
- Check that the error is a 500-level error (not 4xx)

### Problem: Server won't start
**Solution:**
- Check for import errors
- Make sure all dependencies are installed: `poetry install`
- Check that database connection works (if required)

---

## 📸 Example: What Good Logs Look Like

Here's an example of a perfect JSON log entry:

```json
{
  "timestamp": "2025-11-25T15:35:23",
  "level": "ERROR",
  "logger": "src.main",
  "module": "main",
  "function": "maigie_error_handler",
  "line": 95,
  "message": "MaigieError [500-level]: INTERNAL_SERVER_ERROR - Database connection failed",
  "environment": "development",
  "application": "maigie-backend",
  "error_code": "INTERNAL_SERVER_ERROR",
  "status_code": 500,
  "detail": "Connection pool exhausted",
  "path": "/api/v1/examples/test/error-500",
  "method": "POST",
  "user_agent": "curl/7.81.0",
  "traceback": "Traceback (most recent call last):\n  File \"src/routes/examples.py\", line 280, in test_internal_server_error\n    raise InternalServerError(...)\nInternalServerError: Database connection failed",
  "exception": "InternalServerError: Database connection failed\nTraceback (most recent call last):\n..."
}
```

**Notice:**
- ✅ Starts with `{` and ends with `}`
- ✅ All keys are in quotes
- ✅ Has timestamp, level, message
- ✅ Has traceback for errors
- ✅ Has request context (path, method, user_agent)

---

## 🎓 Summary

You've tested:
1. ✅ Structured JSON logging
2. ✅ Error logging with different levels
3. ✅ Sentry error tracking
4. ✅ Safe error responses

**Everything is working if:**
- Logs look like JSON (with `{` and `}`)
- 500 errors show up in Sentry
- Users get safe error messages
- Full error details are in logs (not sent to users)

**Congratulations! 🎉 Your logging and error tracking is working perfectly!**

