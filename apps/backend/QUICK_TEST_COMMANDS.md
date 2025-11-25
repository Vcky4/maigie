# 🚀 Quick Test Commands - Copy & Paste

## Prerequisites
1. Server must be running: `npx nx serve backend`
2. Open a NEW terminal for these commands

---

## Test 1: Structured Logging ✅
```powershell
curl -X POST http://localhost:8000/api/v1/examples/test/structured-logging
```
**Expected:** Success message + 4 JSON logs in server terminal

---

## Test 2: 404 Error (Not Found) ✅
```powershell
curl http://localhost:8000/api/v1/examples/ai/process/nonexistent
```
**Expected:** 404 error response + WARNING log (NOT in Sentry)

---

## Test 3: 500 Error (Internal Server Error) ✅
```powershell
curl -X POST http://localhost:8000/api/v1/examples/test/error-500
```
**Expected:** 
- Generic 500 response
- ERROR log with traceback
- **Check Sentry dashboard** - error should appear there!

---

## Test 4: Unhandled Exception ✅
```powershell
curl -X POST http://localhost:8000/api/v1/examples/test/unhandled-exception
```
**Expected:**
- Generic 500 response
- ERROR log with ZeroDivisionError
- **Check Sentry dashboard** - error should appear there!

---

## Test 5: 403 Error (Subscription Limit) ✅
```powershell
curl -X POST http://localhost:8000/api/v1/examples/ai/voice-session -H "Content-Type: application/json" -H "X-User-Subscription: basic" -d "{\"session_type\": \"conversation\"}"
```
**Expected:** 403 error response + WARNING log (NOT in Sentry)

---

## 🎯 Success Checklist

After running all tests:

- [ ] All commands returned responses (no connection errors)
- [ ] Server terminal shows JSON logs (with `{` and `}`)
- [ ] 500 errors show ERROR level logs with traceback
- [ ] 4xx errors show WARNING level logs (no traceback)
- [ ] Sentry dashboard shows 500 errors (but NOT 4xx errors)
- [ ] All error responses are generic (no secrets leaked)

---

## 🔍 How to Check Sentry

1. Go to: https://sentry.io
2. Open your project
3. Click "Issues" in the left menu
4. Look for errors with today's date
5. Click an error to see full details (traceback, request info, etc.)

---

## 📝 What Each Test Proves

| Test | What It Proves |
|------|----------------|
| Test 1 | JSON logging works, custom fields appear |
| Test 2 | 4xx errors log correctly, don't go to Sentry |
| Test 3 | 500 errors log with traceback, go to Sentry |
| Test 4 | Unhandled exceptions are caught and logged |
| Test 5 | Different error types are handled correctly |

---

## ⚠️ Common Issues

**Problem:** "Connection refused" or "Could not connect"
- **Fix:** Make sure server is running: `npx nx serve backend`

**Problem:** No errors in Sentry
- **Fix:** Check `.env` file has `SENTRY_DSN=...` and restart server

**Problem:** Logs not in JSON format
- **Fix:** Restart server, check `python-json-logger` is installed

---

## 🎉 All Tests Passed?

If all tests work, your logging and error tracking is **100% complete**! ✅

