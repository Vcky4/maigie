"""
Audit QuizSession.topic_id for orphaned references before adding FK constraint.

This script identifies quiz sessions with topic_id values that don't resolve
to existing PrepTopic records, so they can be cleaned up before the foreign
key constraint is added.
"""

import asyncio
import sys
from pathlib import Path

from sqlalchemy import and_, func, select

# Add parent directory to path so we can import from src
sys.path.insert(0, str(Path(__file__).parent.parent))

from src.domains.personal_learning.db_models import PrepTopic, QuizSession  # noqa: E402
from src.shared.database.session import get_session_factory  # noqa: E402


async def audit_orphaned_topic_ids():
    """Find QuizSession records with topic_id that don't reference existing PrepTopic."""
    factory = get_session_factory()

    async with factory() as session:
        # Query 1: Count total quiz sessions with topic_id set
        stmt_total = select(func.count(QuizSession.id)).where(QuizSession.topic_id.isnot(None))
        result = await session.execute(stmt_total)
        total_with_topic = result.scalar() or 0

        print(f"Total QuizSession records with topic_id set: {total_with_topic}")

        if total_with_topic == 0:
            print("✅ No quiz sessions have topic_id set. FK constraint can be added safely.")
            return

        # Query 2: Find orphaned references (topic_id not in PrepTopic)
        # Left join PrepTopic and find where PrepTopic.id is NULL
        stmt_orphaned = (
            select(
                QuizSession.id,
                QuizSession.topic_id,
                QuizSession.prep_id,
                QuizSession.mode,
                QuizSession.status,
                QuizSession.created_at,
            )
            .outerjoin(PrepTopic, QuizSession.topic_id == PrepTopic.id)
            .where(
                and_(
                    QuizSession.topic_id.isnot(None), PrepTopic.id.is_(None)  # Topic doesn't exist
                )
            )
        )

        result = await session.execute(stmt_orphaned)
        orphaned = result.all()

        if not orphaned:
            print("✅ All topic_id references are valid. FK constraint can be added safely.")
            return

        print(f"\n⚠️  Found {len(orphaned)} orphaned references:\n")
        print(
            f"{'Quiz ID':<26} {'Topic ID':<26} {'Prep ID':<26} {'Mode':<20} {'Status':<15} {'Created'}"
        )
        print("-" * 150)

        for row in orphaned:
            quiz_id, topic_id, prep_id, mode, status, created = row
            created_str = created.strftime("%Y-%m-%d %H:%M") if created else "N/A"
            print(
                f"{quiz_id:<26} {topic_id:<26} {prep_id:<26} {mode:<20} {status:<15} {created_str}"
            )

        # Query 3: Group by status to understand what needs cleanup
        stmt_by_status = (
            select(QuizSession.status, func.count(QuizSession.id).label("count"))
            .outerjoin(PrepTopic, QuizSession.topic_id == PrepTopic.id)
            .where(and_(QuizSession.topic_id.isnot(None), PrepTopic.id.is_(None)))
            .group_by(QuizSession.status)
        )

        result = await session.execute(stmt_by_status)
        by_status = result.all()

        print("\n📊 Breakdown by status:")
        for status, count in by_status:
            print(f"  {status}: {count}")

        print("\n💡 Recommendation:")
        print(f"   - Set topic_id to NULL for these {len(orphaned)} records")
        print("   - Or delete them if they're test data")
        print("   - Then add FK constraint with: ForeignKey('PrepTopic.id', ondelete='SET NULL')")


async def main():
    print("🔍 Auditing QuizSession.topic_id for orphaned references...\n")

    # Initialize database connection
    from src.shared.database.session import connect_db

    await connect_db()

    await audit_orphaned_topic_ids()
    print("\n✅ Audit complete.")


if __name__ == "__main__":
    asyncio.run(main())
