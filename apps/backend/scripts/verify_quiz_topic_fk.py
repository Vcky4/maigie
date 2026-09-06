"""
Verify that the foreign key constraint on QuizSession.topic_id exists.
"""

import asyncio
import sys
from pathlib import Path

from sqlalchemy import text

sys.path.insert(0, str(Path(__file__).parent.parent))

from src.shared.database.session import connect_db, get_session_factory  # noqa: E402


async def verify_fk():
    """Check if the foreign key constraint exists."""
    await connect_db()
    factory = get_session_factory()

    async with factory() as session:
        # Query PostgreSQL information schema for the foreign key
        query = text(
            """
            SELECT
                tc.constraint_name,
                tc.table_name,
                kcu.column_name,
                ccu.table_name AS foreign_table_name,
                ccu.column_name AS foreign_column_name,
                rc.update_rule,
                rc.delete_rule
            FROM information_schema.table_constraints AS tc
            JOIN information_schema.key_column_usage AS kcu
              ON tc.constraint_name = kcu.constraint_name
              AND tc.table_schema = kcu.table_schema
            JOIN information_schema.constraint_column_usage AS ccu
              ON ccu.constraint_name = tc.constraint_name
              AND ccu.table_schema = tc.table_schema
            JOIN information_schema.referential_constraints AS rc
              ON rc.constraint_name = tc.constraint_name
            WHERE tc.constraint_type = 'FOREIGN KEY'
              AND tc.table_name = 'QuizSession'
              AND kcu.column_name = 'topicId';
        """
        )

        result = await session.execute(query)
        rows = result.fetchall()

        if not rows:
            print("❌ Foreign key constraint NOT found on QuizSession.topicId")
            return False

        print("✅ Foreign key constraint exists on QuizSession.topicId\n")
        for row in rows:
            print(f"Constraint Name: {row.constraint_name}")
            print(f"Table: {row.table_name}")
            print(f"Column: {row.column_name}")
            print(f"References: {row.foreign_table_name}.{row.foreign_column_name}")
            print(f"On Update: {row.update_rule}")
            print(f"On Delete: {row.delete_rule}")

        # Check for index
        index_query = text(
            """
            SELECT indexname, indexdef
            FROM pg_indexes
            WHERE tablename = 'QuizSession'
              AND indexdef LIKE '%topicId%';
        """
        )

        result = await session.execute(index_query)
        indexes = result.fetchall()

        if indexes:
            print(f"\n📊 Found {len(indexes)} index(es) on topicId:")
            for idx in indexes:
                print(f"  - {idx.indexname}")
        else:
            print("\n⚠️  No index found on topicId")

        return True


async def main():
    print("🔍 Verifying QuizSession.topicId foreign key constraint...\n")
    await verify_fk()


if __name__ == "__main__":
    asyncio.run(main())
