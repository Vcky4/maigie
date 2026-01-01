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
import sys
from pathlib import Path

# Add src directory to path so we can import from src
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from datetime import datetime, timedelta

from prisma import Prisma
from src.core.security import get_password_hash

# Credit limits per tier (matching credit_service.py)
CREDIT_LIMITS = {
    "FREE": {
        "hard_cap": 10000,
        "soft_cap": 8000,
        "daily_limit": 1500,
    },
    "PREMIUM_MONTHLY": {
        "hard_cap": 100000,
        "soft_cap": 80000,
    },
    "PREMIUM_YEARLY": {
        "hard_cap": 1200000,
        "soft_cap": 960000,
    },
}


async def main() -> None:
    """Seed the database with default data."""
    prisma = Prisma()
    await prisma.connect()

    try:
        # Create default admin user (idempotent)
        # Password can be set via ADMIN_PASSWORD env var, defaults to "admin123"
        admin_password = os.getenv("ADMIN_PASSWORD", "admin123")
        admin_password_hash = get_password_hash(admin_password)

        # Prepare credit initialization data
        tier = "PREMIUM_MONTHLY"
        limits = CREDIT_LIMITS[tier]
        period_start = datetime.utcnow()
        period_end = period_start + timedelta(days=30)  # Monthly for PREMIUM_MONTHLY

        admin = await prisma.user.upsert(
            where={"email": "admin@maigie.com"},
            data={
                "create": {
                    "email": "admin@maigie.com",
                    "name": "Admin User",
                    "passwordHash": admin_password_hash,
                    "provider": "email",
                    "tier": tier,
                    "role": "ADMIN",
                    "isActive": True,
                    "isOnboarded": True,
                    "creditsUsed": 0,
                    "creditsPeriodStart": period_start,
                    "creditsPeriodEnd": period_end,
                    "creditsSoftCap": limits["soft_cap"],
                    "creditsHardCap": limits["hard_cap"],
                    "preferences": {
                        "create": {
                            "theme": "light",
                            "language": "en",
                            "notifications": True,
                        }
                    },
                },
                "update": {
                    # Update password if it changed (useful for resetting)
                    "passwordHash": admin_password_hash,
                    # Ensure role stays ADMIN
                    "role": "ADMIN",
                    # Ensure user stays active
                    "isActive": True,
                    # Reset credits if needed
                    "creditsUsed": 0,
                    "creditsPeriodStart": period_start,
                    "creditsPeriodEnd": period_end,
                    "creditsSoftCap": limits["soft_cap"],
                    "creditsHardCap": limits["hard_cap"],
                },
            },
            include={"preferences": True},
        )

        print(f"✓ Seeded admin user: {admin.email}")
        print(f"  Role: {admin.role}")
        print(
            f"  Password: {'(from ADMIN_PASSWORD env var)' if os.getenv('ADMIN_PASSWORD') else '(default: admin123)'}"
        )

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
