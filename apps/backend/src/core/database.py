"""
Database connection utilities using Prisma.
This manages the global database connection lifecycle.

Copyright (C) 2025 Maigie

This program is free software: you can redistribute it and/or modify
it under the terms of the GNU Affero General Public License as published
by the Free Software Foundation, either version 3 of the License, or
(at your option) any later version.

This program is distributed in the hope that it will be useful,
but WITHOUT ANY WARRANTY; without even the implied warranty of
MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
GNU Affero General Public License for more details.

You should have received a copy of the GNU Affero General Public License
along with this program.  If not, see <https://www.gnu.org/licenses/>.
"""

from prisma import Prisma

# 1. Global Database Instance
# We instantiate this ONCE. Prisma handles the connection pooling internally.
db = Prisma()


async def connect_db() -> None:
    """
    Connect to the database.
    This should be called during the FastAPI startup event.
    """
    if not db.is_connected():
        await db.connect()
        print(" Database Connected")


async def disconnect_db() -> None:
    """
    Disconnect from the database.
    This should be called during the FastAPI shutdown event.
    """
    if db.is_connected():
        await db.disconnect()
        print(" Database Disconnected")


async def check_db_health() -> dict:
    """
    Perform a real query to ensure the database is responsive.
    """
    try:
        if not db.is_connected():
            return {"status": "disconnected", "type": "postgresql"}

        # Run a simple raw query to check connectivity
        # This is better than just checking .is_connected()
        await db.query_raw("SELECT 1")

        return {"status": "healthy", "type": "postgresql", "engine": "prisma"}
    except Exception as e:
        return {"status": "unhealthy", "error": str(e), "type": "postgresql"}
