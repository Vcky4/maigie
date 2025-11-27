"""
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

Database seed script for preview environments.

This script seeds the database with default data for preview/test environments.
It should be safe to run multiple times (idempotent).
"""

import asyncio
import os
from prisma import Prisma


async def main() -> None:
    """Seed the database with default data."""
    prisma = Prisma()
    await prisma.connect()

    try:
        # Example: Create default admin user (idempotent)
        # Uncomment and customize as needed
        # admin = await prisma.user.upsert(
        #     where={"email": "admin@maigie.com"},
        #     data={
        #         "create": {
        #             "email": "admin@maigie.com",
        #             "name": "Admin User",
        #             "tier": "PREMIUM_MONTHLY",
        #         },
        #         "update": {},
        #     },
        # )
        # print(f"✓ Seeded admin user: {admin.email}")

        # Example: Create test users for preview environments
        # test_user = await prisma.user.upsert(
        #     where={"email": "test@maigie.com"},
        #     data={
        #         "create": {
        #             "email": "test@maigie.com",
        #             "name": "Test User",
        #             "tier": "FREE",
        #         },
        #         "update": {},
        #     },
        # )
        # print(f"✓ Seeded test user: {test_user.email}")

        # Add more seed data as needed
        # For example: sample courses, goals, resources, etc.

        print("✓ Database seeded successfully!")
        print(f"  Environment: {os.getenv('ENVIRONMENT', 'unknown')}")
        print(f"  Preview ID: {os.getenv('PREVIEW_ID', 'N/A')}")
    except Exception as e:
        print(f"✗ Error seeding database: {e}")
        raise
    finally:
        await prisma.disconnect()


if __name__ == "__main__":
    asyncio.run(main())
