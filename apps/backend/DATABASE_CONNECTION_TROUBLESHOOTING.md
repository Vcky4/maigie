# Database Connection Troubleshooting

**Error**: `socket.gaierror: [Errno 11001] getaddrinfo failed`

This means the hostname `db.drycclwusluoxpbeyaff.supabase.co` cannot be resolved.

---

## Quick Diagnosis

### 1. Check Internet Connection

```powershell
ping google.com
```

If this fails, you have no internet connection.

### 2. Test DNS Resolution

```powershell
nslookup db.drycclwusluoxpbeyaff.supabase.co
```

**Expected**: Should return an IP address  
**If fails**: DNS issue - try changing DNS servers

### 3. Test Direct Connection

```powershell
Test-NetConnection -ComputerName db.drycclwusluoxpbeyaff.supabase.co -Port 5432
```

**Expected**: `TcpTestSucceeded : True`  
**If fails**: Port blocked or service down

### 4. Check Supabase Status

Visit: https://status.supabase.com/

Check if there are any ongoing incidents.

---

## Solutions

### If DNS Issue

**Option 1: Change DNS to Google DNS**

```powershell
# Run as Administrator
Set-DnsClientServerAddress -InterfaceAlias "Wi-Fi" -ServerAddresses ("8.8.8.8","8.8.4.4")
```

**Option 2: Flush DNS Cache**

```powershell
# Run as Administrator
ipconfig /flushdns
```

**Option 3: Add to hosts file** (temporary workaround)

```powershell
# Run as Administrator
notepad C:\Windows\System32\drivers\etc\hosts
```

Add line (get IP from nslookup on working machine):
```
<IP_ADDRESS> db.drycclwusluoxpbeyaff.supabase.co
```

### If Network/Firewall Issue

1. **Check Windows Firewall**:
   - Allow outbound connections on port 5432
   - Temporarily disable to test

2. **Check Corporate VPN/Proxy**:
   - VPN might block database connections
   - Try disconnecting VPN

3. **Check Antivirus**:
   - Some antivirus software blocks PostgreSQL connections
   - Add exception for Python/PostgreSQL

### If Supabase Service Down

**Wait and retry** - Check status page

Or **use local PostgreSQL** for development:

1. Install PostgreSQL locally
2. Update `.env`:
   ```env
   DATABASE_URL=postgresql://postgres:postgres@localhost:5432/maigie_dev
   ```

---

## Alternative: Run Migration Later

Since the implementation is **code-complete**, you can:

1. **Skip migration now** - All code is ready
2. **Test without database** - Review code changes
3. **Run migration when connection available** - Or on deployment environment

The migration is safe and ready to run anytime.

---

## Current Status

✅ **Implementation Complete**: All code written  
✅ **Migration Ready**: `015_add_onboarding_state_fields.py` prepared  
❌ **Database Accessible**: Connection failing  

**What you can do now**:
- Review all the documentation created
- Plan testing scenarios
- Test mobile UI in simulator (mocked API)
- Wait for network/database access

**What needs database**:
- Running the migration
- Testing backend endpoints
- End-to-end flow testing

---

## When Connection Works

Run this to apply all changes:

```bash
cd apps/backend
poetry run alembic upgrade head
```

Then verify:

```sql
\d+ "LearningProfile"
```

Should show the 5 new columns.
