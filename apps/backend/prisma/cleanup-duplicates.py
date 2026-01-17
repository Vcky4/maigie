#!/usr/bin/env python3
"""
Cleanup script to remove duplicate stripeCustomerId and stripeSubscriptionId values
before adding unique constraints.

This script:
1. Finds duplicate stripeCustomerId values and keeps only the first one
2. Finds duplicate stripeSubscriptionId values and keeps only the first one
3. Sets duplicates to NULL to allow unique constraint to be added
"""

import sys

from prisma import Prisma


async def cleanup_duplicates():
    """Clean up duplicate Stripe IDs before adding unique constraints."""
    db = Prisma()
    await db.connect()

    try:
        # Fetch all users ordered by creation date
        print("Fetching all users...")
        all_users = await db.user.find_many(order_by={"createdAt": "asc"})

        print(f"Found {len(all_users)} users")

        # Find and fix duplicate stripeCustomerId values
        print("Checking for duplicate stripeCustomerId values...")
        seen_customer_ids = set()
        duplicates_fixed = 0

        for user in all_users:
            if user.stripeCustomerId and user.stripeCustomerId in seen_customer_ids:
                print(
                    f"Found duplicate stripeCustomerId: {user.stripeCustomerId} for user {user.id} ({user.email})"
                )
                await db.user.update(where={"id": user.id}, data={"stripeCustomerId": None})
                duplicates_fixed += 1
            elif user.stripeCustomerId:
                seen_customer_ids.add(user.stripeCustomerId)

        print(f"Fixed {duplicates_fixed} duplicate stripeCustomerId values")

        # Find and fix duplicate stripeSubscriptionId values
        print("Checking for duplicate stripeSubscriptionId values...")
        seen_subscription_ids = set()
        duplicates_fixed_sub = 0

        for user in all_users:
            if user.stripeSubscriptionId and user.stripeSubscriptionId in seen_subscription_ids:
                print(
                    f"Found duplicate stripeSubscriptionId: {user.stripeSubscriptionId} for user {user.id} ({user.email})"
                )
                await db.user.update(where={"id": user.id}, data={"stripeSubscriptionId": None})
                duplicates_fixed_sub += 1
            elif user.stripeSubscriptionId:
                seen_subscription_ids.add(user.stripeSubscriptionId)

        print(f"Fixed {duplicates_fixed_sub} duplicate stripeSubscriptionId values")

        total_fixed = duplicates_fixed + duplicates_fixed_sub
        if total_fixed > 0:
            print(f"Cleanup complete! Fixed {total_fixed} duplicate values.")
        else:
            print("No duplicates found. Database is ready for unique constraints.")

        return total_fixed

    except Exception as e:
        print(f"Error during cleanup: {e}", file=sys.stderr)
        import traceback

        traceback.print_exc()
        sys.exit(1)
    finally:
        await db.disconnect()


if __name__ == "__main__":
    import asyncio

    asyncio.run(cleanup_duplicates())
