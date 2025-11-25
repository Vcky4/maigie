# 🚀 START HERE - Testing Your Logging & Error Tracking

## 👋 Welcome!

This guide will help you test everything step-by-step. Don't worry, it's super simple!

---

## 📚 Which Guide Should I Use?

### 🎯 **For Complete Beginners (Start Here!):**
👉 **`TESTING_GUIDE_SIMPLE.md`** 
- Step-by-step instructions
- Explains everything in simple terms
- Shows exactly what to look for
- Perfect if you're new to this

### ⚡ **For Quick Testing:**
👉 **`QUICK_TEST_COMMANDS.md`**
- Just copy and paste commands
- Quick reference
- Good if you know what you're doing

### 👀 **For Visual Learners:**
👉 **`VISUAL_TESTING_GUIDE.md`**
- Shows what success looks like
- Visual examples
- Red flags vs green flags

### 🤖 **For Automated Testing:**
👉 **`test_logging_simple.ps1`**
- Run the script, it does everything
- Good for quick verification

---

## 🎬 Quick Start (3 Steps)

### Step 1: Start Your Server
```powershell
cd apps/backend
npx nx serve backend
```

**Wait for:** `INFO: Application startup complete.`

### Step 2: Run the Test Script
Open a **NEW terminal** and run:
```powershell
cd apps/backend
.\test_logging_simple.ps1
```

### Step 3: Check Results
- ✅ Server terminal shows JSON logs
- ✅ Test script shows all tests passed
- ✅ Sentry dashboard shows 500 errors

**That's it!** 🎉

---

## 📋 Manual Testing (If You Prefer)

### Option A: Use the Simple Guide
1. Open `TESTING_GUIDE_SIMPLE.md`
2. Follow each step
3. Check off items as you go

### Option B: Use Quick Commands
1. Open `QUICK_TEST_COMMANDS.md`
2. Copy and paste each command
3. Check the expected results

---

## ✅ What Success Looks Like

### In Your Server Terminal:
```
{"timestamp": "...", "level": "INFO", "message": "Structured logging configured", ...}
{"timestamp": "...", "level": "ERROR", "message": "MaigieError [500-level]", "traceback": "...", ...}
```
✅ All logs are in JSON format (have `{` and `}`)

### In Your Test Terminal:
```json
{"status_code": 500, "code": "INTERNAL_SERVER_ERROR", "message": "An internal server error occurred. Please try again later."}
```
✅ Error messages are generic and safe

### In Sentry Dashboard:
```
🚨 InternalServerError: This is a test error
📍 /api/v1/examples/test/error-500
🕐 Just now
```
✅ 500 errors appear in Sentry

---

## 🎯 Testing Checklist

After running all tests, verify:

- [ ] **JSON Logging:** All logs have `{` and `}` brackets
- [ ] **Error Logging:** 500 errors show ERROR level with traceback
- [ ] **Sentry Integration:** 500 errors appear in Sentry dashboard
- [ ] **Safe Responses:** Error messages are generic (no secrets)
- [ ] **Correct Levels:** 4xx = WARNING, 5xx = ERROR

---

## 🐛 Common Issues & Quick Fixes

### Issue: "Connection refused"
**Fix:** Make sure server is running: `npx nx serve backend`

### Issue: Logs not in JSON format
**Fix:** Restart server, check `python-json-logger` is installed

### Issue: No errors in Sentry
**Fix:** 
1. Check `.env` file has `SENTRY_DSN=...`
2. Restart server
3. Check Sentry project is correct

### Issue: Can't find test endpoints
**Fix:** Make sure server started successfully (check for errors)

---

## 📖 Full Documentation

- **`LOGGING_AND_ERROR_TRACKING.md`** - Complete technical documentation
- **`BATCH_3_SUMMARY.md`** - What was implemented
- **`QUICK_TEST_GUIDE.md`** - Original quick guide

---

## 🎉 You're Ready!

Pick a guide above and start testing! 

**Recommended:** Start with `TESTING_GUIDE_SIMPLE.md` if you want detailed step-by-step instructions.

**Good luck!** 🚀

