# Simple PowerShell script to test logging and error tracking
# Run this while your server is running

Write-Host "========================================" -ForegroundColor Cyan
Write-Host "  Maigie Logging & Error Tracking Tests" -ForegroundColor Cyan
Write-Host "========================================" -ForegroundColor Cyan
Write-Host ""

# Check if server is running
Write-Host "Step 1: Checking if server is running..." -ForegroundColor Yellow
try {
    $response = Invoke-WebRequest -Uri "http://localhost:8000/" -Method GET -TimeoutSec 2 -ErrorAction Stop
    Write-Host "✅ Server is running!" -ForegroundColor Green
} catch {
    Write-Host "❌ Server is NOT running!" -ForegroundColor Red
    Write-Host "Please start the server first: npx nx serve backend" -ForegroundColor Red
    exit 1
}

Write-Host ""
Write-Host "Step 2: Testing structured logging..." -ForegroundColor Yellow
try {
    $response = Invoke-RestMethod -Uri "http://localhost:8000/api/v1/examples/test/structured-logging" -Method POST
    Write-Host "✅ Structured logging test passed!" -ForegroundColor Green
    Write-Host "   Check server terminal for JSON logs" -ForegroundColor Gray
} catch {
    Write-Host "❌ Structured logging test failed!" -ForegroundColor Red
    Write-Host $_.Exception.Message -ForegroundColor Red
}

Write-Host ""
Write-Host "Step 3: Testing 404 error (should NOT go to Sentry)..." -ForegroundColor Yellow
try {
    $response = Invoke-RestMethod -Uri "http://localhost:8000/api/v1/examples/ai/process/nonexistent" -Method GET -ErrorAction Stop
} catch {
    $statusCode = $_.Exception.Response.StatusCode.value__
    if ($statusCode -eq 404) {
        Write-Host "✅ 404 error test passed! (Status: 404)" -ForegroundColor Green
        Write-Host "   This error should NOT appear in Sentry (correct behavior)" -ForegroundColor Gray
    } else {
        Write-Host "⚠️  Unexpected status code: $statusCode" -ForegroundColor Yellow
    }
}

Write-Host ""
Write-Host "Step 4: Testing 500 error (SHOULD go to Sentry)..." -ForegroundColor Yellow
try {
    $response = Invoke-RestMethod -Uri "http://localhost:8000/api/v1/examples/test/error-500" -Method POST -ErrorAction Stop
} catch {
    $statusCode = $_.Exception.Response.StatusCode.value__
    if ($statusCode -eq 500) {
        Write-Host "✅ 500 error test passed! (Status: 500)" -ForegroundColor Green
        Write-Host "   ⚠️  IMPORTANT: Check Sentry dashboard - this error should appear there!" -ForegroundColor Yellow
        Write-Host "   Check server terminal for ERROR level log with traceback" -ForegroundColor Gray
    } else {
        Write-Host "⚠️  Unexpected status code: $statusCode" -ForegroundColor Yellow
    }
}

Write-Host ""
Write-Host "Step 5: Testing unhandled exception (SHOULD go to Sentry)..." -ForegroundColor Yellow
try {
    $response = Invoke-RestMethod -Uri "http://localhost:8000/api/v1/examples/test/unhandled-exception" -Method POST -ErrorAction Stop
} catch {
    $statusCode = $_.Exception.Response.StatusCode.value__
    if ($statusCode -eq 500) {
        Write-Host "✅ Unhandled exception test passed! (Status: 500)" -ForegroundColor Green
        Write-Host "   ⚠️  IMPORTANT: Check Sentry dashboard - this error should appear there!" -ForegroundColor Yellow
        Write-Host "   Check server terminal for ERROR level log with ZeroDivisionError" -ForegroundColor Gray
    } else {
        Write-Host "⚠️  Unexpected status code: $statusCode" -ForegroundColor Yellow
    }
}

Write-Host ""
Write-Host "========================================" -ForegroundColor Cyan
Write-Host "  Testing Complete!" -ForegroundColor Cyan
Write-Host "========================================" -ForegroundColor Cyan
Write-Host ""
Write-Host "Next Steps:" -ForegroundColor Yellow
Write-Host "1. Check your SERVER terminal for JSON-formatted logs" -ForegroundColor White
Write-Host "2. Check Sentry dashboard for 500-level errors" -ForegroundColor White
Write-Host "3. Verify logs are in JSON format (have { and } brackets)" -ForegroundColor White
Write-Host "4. Verify 500 errors have full traceback in logs" -ForegroundColor White
Write-Host ""
Write-Host "For detailed guide, see: TESTING_GUIDE_SIMPLE.md" -ForegroundColor Gray

