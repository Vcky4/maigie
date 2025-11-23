# apps/backend/src/core/database.py
from prisma import Prisma

# 1. Global Database Instance
# We create it once here so we can import 'db' anywhere in the app
db = Prisma()

# 2. Connection Function (for Startup)
async def connect_db():
    if not db.is_connected():
        await db.connect()
        print("Database Connected")

# 3. Disconnect Function (for Shutdown)
async def disconnect_db():
    if db.is_connected():
        await db.disconnect()
        print(" Database Disconnected")