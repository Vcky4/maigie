#!/usr/bin/env python
"""
Database Schema Sync — ensures all tables and columns exist.

This script brings an existing database (created by Prisma or partial migrations)
into alignment with the current SQLAlchemy models. Safe to run multiple times.

Usage:
    poetry run python scripts/sync_schema.py

What it does:
1. Adds missing columns to existing tables (Note, ExamPrep, etc.)
2. Creates all 15 new personal learning tables if they don't exist
3. Stamps the Alembic version to the latest migration

All operations use IF NOT EXISTS / IF EXISTS patterns for idempotency.
"""

import asyncio
import os
import sys

# Add project root to path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


async def _exec(conn, sql: str, *, optional: bool = False):
    """Execute SQL with logging, using SAVEPOINTs for isolation.

    Each statement runs in its own savepoint so a failure doesn't
    poison the entire transaction.
    """
    from sqlalchemy import text
    import uuid

    short = sql.strip().split("\n")[0][:80]
    sp_name = f"sp_{uuid.uuid4().hex[:8]}"

    try:
        # Create savepoint
        await conn.execute(text(f"SAVEPOINT {sp_name}"))
        await conn.execute(text(sql))
        # Release savepoint on success
        await conn.execute(text(f"RELEASE SAVEPOINT {sp_name}"))
        print(f"  ✓ {short}")
    except Exception as e:
        # Rollback to savepoint so transaction stays usable
        try:
            await conn.execute(text(f"ROLLBACK TO SAVEPOINT {sp_name}"))
        except Exception:
            pass  # If rollback itself fails, nothing we can do

        err_str = str(e).lower()
        if "already exists" in err_str or "duplicate" in err_str:
            print(f"  ○ {short} (already exists)")
        elif optional or "does not exist" in err_str:
            print(f"  ⊘ {short} (skipped — referenced table/column missing)")
        else:
            print(f"  ✗ {short}")
            print(f"    Error: {e}")
            raise


async def main():
    from sqlalchemy.ext.asyncio import create_async_engine

    # Load settings
    from src.config import get_settings

    settings = get_settings()

    database_url = settings.DATABASE_URL
    if not database_url:
        print("ERROR: DATABASE_URL not configured")
        sys.exit(1)

    # Convert to async URL format (same logic as src/shared/database/session.py)
    if database_url.startswith("postgresql://"):
        database_url = database_url.replace("postgresql://", "postgresql+asyncpg://", 1)
    elif database_url.startswith("postgres://"):
        database_url = database_url.replace("postgres://", "postgresql+asyncpg://", 1)

    # Strip pgbouncer param — asyncpg doesn't support it as a URL param
    if "?pgbouncer=true" in database_url:
        database_url = database_url.replace("?pgbouncer=true", "")
    elif "&pgbouncer=true" in database_url:
        database_url = database_url.replace("&pgbouncer=true", "")

    print(f"Connecting to database...")
    engine = create_async_engine(database_url, echo=False)

    async with engine.begin() as conn:
        print("\n=== Phase 0: Circle → Space rename ===\n")

        # Rename tables (IF the old name exists and new name doesn't)
        table_renames = [
            ("Circle", "Space"),
            ("CircleMember", "SpaceMember"),
            ("CircleChatGroup", "SpaceChatGroup"),
            ("CircleChatGroupMember", "SpaceChatGroupMember"),
            ("CircleInvite", "SpaceInvite"),
            ("CircleMemberStat", "SpaceMemberStat"),
            ("CircleSession", "SpaceSession"),
            ("CircleJoinRequest", "SpaceJoinRequest"),
            ("CircleSubscription", "SpaceSubscription"),
            ("CircleSeatAddon", "SpaceSeatAddon"),
        ]

        for old_name, new_name in table_renames:
            await _exec(
                conn,
                f"""
                DO $$
                BEGIN
                    IF EXISTS (SELECT 1 FROM information_schema.tables WHERE table_name = '{old_name}')
                       AND NOT EXISTS (SELECT 1 FROM information_schema.tables WHERE table_name = '{new_name}') THEN
                        ALTER TABLE "{old_name}" RENAME TO "{new_name}";
                        RAISE NOTICE 'Renamed {old_name} → {new_name}';
                    END IF;
                END $$;
            """,
                optional=True,
            )

        # Rename columns (circleId → spaceId, etc.)
        # Each entry: (table_name_after_rename, old_col, new_col)
        column_renames = [
            ("Space", "circlePlanActive", "spacePlanActive"),
            ("Space", "circlePlanCurrentPeriodEnd", "spacePlanCurrentPeriodEnd"),
            ("SpaceMember", "circleId", "spaceId"),
            ("SpaceChatGroup", "circleId", "spaceId"),
            ("SpaceInvite", "circleId", "spaceId"),
            ("SpaceMemberStat", "circleId", "spaceId"),
            ("SpaceSession", "circleId", "spaceId"),
            ("SpaceJoinRequest", "circleId", "spaceId"),
            ("SpaceSubscription", "circleId", "spaceId"),
            ("SpaceSeatAddon", "circleId", "spaceId"),
            ("AiUsageRecord", "circleId", "spaceId"),
            ("ChatSession", "circleId", "spaceId"),
            ("ChatSession", "isCircleRoom", "isSpaceRoom"),
            ("Note", "circleId", "spaceId"),
            ("Course", "circleId", "spaceId"),
            ("Resource", "circleId", "spaceId"),
            ("Goal", "circleId", "spaceId"),
            ("LearningInsight", "circleId", "spaceId"),
        ]

        for table, old_col, new_col in column_renames:
            await _exec(
                conn,
                f"""
                DO $$
                BEGIN
                    IF EXISTS (
                        SELECT 1 FROM information_schema.columns
                        WHERE table_name = '{table}' AND column_name = '{old_col}'
                    ) THEN
                        ALTER TABLE "{table}" RENAME COLUMN "{old_col}" TO "{new_col}";
                    END IF;
                END $$;
            """,
                optional=True,
            )

        # Rename indexes
        index_renames = [
            ("Circle_visibility_idx", "Space_visibility_idx"),
            ("Circle_featured_idx", "Space_featured_idx"),
            ("Circle_circlePlanActive_idx", "Space_spacePlanActive_idx"),
            ("CircleMember_circleId_userId_key", "SpaceMember_spaceId_userId_key"),
            ("CircleMember_circleId_seatTier_idx", "SpaceMember_spaceId_seatTier_idx"),
            (
                "CircleChatGroupMember_chatGroupId_userId_key",
                "SpaceChatGroupMember_chatGroupId_userId_key",
            ),
            ("CircleInvite_circleId_inviteeEmail_key", "SpaceInvite_spaceId_inviteeEmail_key"),
            ("CircleMemberStat_circleId_userId_key", "SpaceMemberStat_spaceId_userId_key"),
            ("CircleJoinRequest_circleId_userId_key", "SpaceJoinRequest_spaceId_userId_key"),
            ("CircleJoinRequest_circleId_status_idx", "SpaceJoinRequest_spaceId_status_idx"),
            ("CircleSeatAddon_circleId_status_idx", "SpaceSeatAddon_spaceId_status_idx"),
            ("CircleSeatAddon_circleId_assignedAt_idx", "SpaceSeatAddon_spaceId_assignedAt_idx"),
            ("ChatSession_isCircleRoom_idx", "ChatSession_isSpaceRoom_idx"),
            ("ChatSession_userId_circleId_courseId_idx", "ChatSession_userId_spaceId_courseId_idx"),
            ("ChatSession_userId_circleId_topicId_idx", "ChatSession_userId_spaceId_topicId_idx"),
            ("AiUsageRecord_circleId_userId_idx", "AiUsageRecord_spaceId_userId_idx"),
            ("AiUsageRecord_circleId_createdAt_idx", "AiUsageRecord_spaceId_createdAt_idx"),
        ]

        for old_name, new_name in index_renames:
            await _exec(
                conn,
                f"""ALTER INDEX IF EXISTS "{old_name}" RENAME TO "{new_name}";""",
                optional=True,
            )

        print("\n=== Phase 1: Add missing columns to existing tables ===\n")

        # Note table — add spaceId and lastEditedById
        await _exec(
            conn,
            """
            ALTER TABLE "Note" ADD COLUMN IF NOT EXISTS "spaceId" VARCHAR NULL;
        """,
        )
        await _exec(
            conn,
            """
            ALTER TABLE "Note" ADD COLUMN IF NOT EXISTS "lastEditedById" VARCHAR NULL;
        """,
        )

        # ExamPrep table — add spaceId
        await _exec(
            conn,
            """
            ALTER TABLE "ExamPrep" ADD COLUMN IF NOT EXISTS "spaceId" VARCHAR NULL;
        """,
        )

        # Add FK constraints for the new columns (idempotent: check before adding)
        await _exec(
            conn,
            """
            DO $$
            BEGIN
                IF NOT EXISTS (
                    SELECT 1 FROM information_schema.table_constraints
                    WHERE constraint_name = 'Note_spaceId_fkey'
                    AND table_name = 'Note'
                ) THEN
                    ALTER TABLE "Note"
                        ADD CONSTRAINT "Note_spaceId_fkey"
                        FOREIGN KEY ("spaceId") REFERENCES "Space"(id)
                        ON DELETE SET NULL;
                END IF;
            END $$;
        """,
            optional=True,
        )
        await _exec(
            conn,
            """
            DO $$
            BEGIN
                IF NOT EXISTS (
                    SELECT 1 FROM information_schema.table_constraints
                    WHERE constraint_name = 'Note_lastEditedById_fkey'
                    AND table_name = 'Note'
                ) THEN
                    ALTER TABLE "Note"
                        ADD CONSTRAINT "Note_lastEditedById_fkey"
                        FOREIGN KEY ("lastEditedById") REFERENCES "User"(id)
                        ON DELETE SET NULL;
                END IF;
            END $$;
        """,
            optional=True,
        )
        await _exec(
            conn,
            """
            DO $$
            BEGIN
                IF NOT EXISTS (
                    SELECT 1 FROM information_schema.table_constraints
                    WHERE constraint_name = 'ExamPrep_spaceId_fkey'
                    AND table_name = 'ExamPrep'
                ) THEN
                    ALTER TABLE "ExamPrep"
                        ADD CONSTRAINT "ExamPrep_spaceId_fkey"
                        FOREIGN KEY ("spaceId") REFERENCES "Space"(id)
                        ON DELETE SET NULL;
                END IF;
            END $$;
        """,
            optional=True,
        )

        # Add indexes for the new columns
        await _exec(
            conn,
            """CREATE INDEX IF NOT EXISTS "Note_spaceId_idx" ON "Note"("spaceId");""",
            optional=True,
        )
        await _exec(
            conn,
            """CREATE INDEX IF NOT EXISTS "ExamPrep_spaceId_idx" ON "ExamPrep"("spaceId");""",
            optional=True,
        )

        print("\n=== Phase 2: Create new personal learning tables ===\n")

        # FlashcardDeck
        await _exec(
            conn,
            """
            CREATE TABLE IF NOT EXISTS "FlashcardDeck" (
                id VARCHAR PRIMARY KEY,
                "userId" VARCHAR NOT NULL REFERENCES "User"(id) ON DELETE CASCADE,
                title VARCHAR NOT NULL,
                description VARCHAR,
                "courseId" VARCHAR,
                "topicId" VARCHAR,
                "prepId" VARCHAR REFERENCES "ExamPrep"(id) ON DELETE SET NULL,
                "createdAt" TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                "updatedAt" TIMESTAMPTZ NOT NULL DEFAULT NOW()
            );
        """,
        )
        await _exec(
            conn,
            """CREATE INDEX IF NOT EXISTS "FlashcardDeck_userId_idx" ON "FlashcardDeck"("userId");""",
        )
        await _exec(
            conn,
            """CREATE INDEX IF NOT EXISTS "FlashcardDeck_courseId_idx" ON "FlashcardDeck"("courseId");""",
        )
        await _exec(
            conn,
            """CREATE INDEX IF NOT EXISTS "FlashcardDeck_topicId_idx" ON "FlashcardDeck"("topicId");""",
        )

        # Flashcard
        await _exec(
            conn,
            """
            CREATE TABLE IF NOT EXISTS "Flashcard" (
                id VARCHAR PRIMARY KEY,
                "userId" VARCHAR NOT NULL REFERENCES "User"(id) ON DELETE CASCADE,
                "deckId" VARCHAR REFERENCES "FlashcardDeck"(id) ON DELETE SET NULL,
                front TEXT NOT NULL,
                back TEXT NOT NULL,
                "intervalDays" INTEGER NOT NULL DEFAULT 1,
                "repetitionCount" INTEGER NOT NULL DEFAULT 0,
                "easeFactor" FLOAT NOT NULL DEFAULT 2.5,
                "nextReviewAt" TIMESTAMPTZ NOT NULL,
                "lastReviewedAt" TIMESTAMPTZ,
                "lastQuality" INTEGER NOT NULL DEFAULT -1,
                "lapseCount" INTEGER NOT NULL DEFAULT 0,
                "sourceType" VARCHAR,
                "sourceId" VARCHAR,
                "createdAt" TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                "updatedAt" TIMESTAMPTZ NOT NULL DEFAULT NOW()
            );
        """,
        )
        await _exec(
            conn,
            """CREATE INDEX IF NOT EXISTS "Flashcard_userId_nextReviewAt_idx" ON "Flashcard"("userId", "nextReviewAt");""",
        )
        await _exec(
            conn,
            """CREATE INDEX IF NOT EXISTS "Flashcard_deckId_idx" ON "Flashcard"("deckId");""",
        )
        await _exec(
            conn,
            """CREATE INDEX IF NOT EXISTS "Flashcard_userId_idx" ON "Flashcard"("userId");""",
        )

        # SavedResource
        await _exec(
            conn,
            """
            CREATE TABLE IF NOT EXISTS "SavedResource" (
                id VARCHAR PRIMARY KEY,
                "userId" VARCHAR NOT NULL REFERENCES "User"(id) ON DELETE CASCADE,
                title VARCHAR NOT NULL,
                url VARCHAR,
                "sourceType" VARCHAR NOT NULL,
                "sourceId" VARCHAR,
                tags JSONB,
                "lastAccessedAt" TIMESTAMPTZ,
                "createdAt" TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                "updatedAt" TIMESTAMPTZ NOT NULL DEFAULT NOW()
            );
        """,
        )
        await _exec(
            conn,
            """CREATE INDEX IF NOT EXISTS "SavedResource_userId_sourceType_idx" ON "SavedResource"("userId", "sourceType");""",
        )

        # LearningProfile
        await _exec(
            conn,
            """
            CREATE TABLE IF NOT EXISTS "LearningProfile" (
                id VARCHAR PRIMARY KEY,
                "userId" VARCHAR NOT NULL UNIQUE REFERENCES "User"(id) ON DELETE CASCADE,
                purpose VARCHAR,
                subjects JSONB,
                "goalsText" TEXT,
                "preferredExplanationStyle" VARCHAR,
                "proficiencyMap" JSONB,
                "onboardingCompletedAt" TIMESTAMPTZ,
                "maturityDays" INTEGER NOT NULL DEFAULT 0,
                "quietHoursStart" VARCHAR,
                "quietHoursEnd" VARCHAR,
                "maxDailyNotifications" INTEGER NOT NULL DEFAULT 5,
                "preferredStudyTimes" JSONB,
                "avgSessionMinutes" FLOAT,
                "consistencyScore" FLOAT,
                "bestDayOfWeek" VARCHAR,
                "dropoutRisk" FLOAT,
                "createdAt" TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                "updatedAt" TIMESTAMPTZ NOT NULL DEFAULT NOW()
            );
        """,
        )
        await _exec(
            conn,
            """CREATE INDEX IF NOT EXISTS "LearningProfile_userId_idx" ON "LearningProfile"("userId");""",
        )

        # Notification
        await _exec(
            conn,
            """
            CREATE TABLE IF NOT EXISTS "Notification" (
                id VARCHAR PRIMARY KEY,
                "userId" VARCHAR NOT NULL REFERENCES "User"(id) ON DELETE CASCADE,
                type VARCHAR NOT NULL,
                title VARCHAR NOT NULL,
                body TEXT NOT NULL,
                priority INTEGER NOT NULL DEFAULT 5,
                "actionData" JSONB,
                "scheduledAt" TIMESTAMPTZ NOT NULL,
                "deliveredAt" TIMESTAMPTZ,
                "readAt" TIMESTAMPTZ,
                "dismissedAt" TIMESTAMPTZ,
                status VARCHAR NOT NULL DEFAULT 'PENDING',
                "createdAt" TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                "updatedAt" TIMESTAMPTZ NOT NULL DEFAULT NOW()
            );
        """,
        )
        await _exec(
            conn,
            """CREATE INDEX IF NOT EXISTS "Notification_userId_status_idx" ON "Notification"("userId", status);""",
        )
        await _exec(
            conn,
            """CREATE INDEX IF NOT EXISTS "Notification_scheduledAt_idx" ON "Notification"("scheduledAt");""",
        )

        # PrepTopic
        await _exec(
            conn,
            """
            CREATE TABLE IF NOT EXISTS "PrepTopic" (
                id VARCHAR PRIMARY KEY,
                "prepId" VARCHAR NOT NULL REFERENCES "ExamPrep"(id) ON DELETE CASCADE,
                title VARCHAR NOT NULL,
                description TEXT,
                "estimatedMinutes" INTEGER NOT NULL DEFAULT 30,
                "orderIndex" INTEGER NOT NULL DEFAULT 0,
                "masteryScore" FLOAT NOT NULL DEFAULT 0.0,
                status VARCHAR NOT NULL DEFAULT 'NOT_STARTED',
                "createdAt" TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                "updatedAt" TIMESTAMPTZ NOT NULL DEFAULT NOW()
            );
        """,
        )
        await _exec(
            conn,
            """CREATE INDEX IF NOT EXISTS "PrepTopic_prepId_order_idx" ON "PrepTopic"("prepId", "orderIndex");""",
        )

        # PrepMaterial
        await _exec(
            conn,
            """
            CREATE TABLE IF NOT EXISTS "PrepMaterial" (
                id VARCHAR PRIMARY KEY,
                "prepId" VARCHAR NOT NULL REFERENCES "ExamPrep"(id) ON DELETE CASCADE,
                filename VARCHAR NOT NULL,
                url VARCHAR NOT NULL,
                "fileType" VARCHAR,
                size INTEGER,
                "extractedText" TEXT,
                category VARCHAR NOT NULL DEFAULT 'OTHER',
                label VARCHAR,
                "createdAt" TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                "updatedAt" TIMESTAMPTZ NOT NULL DEFAULT NOW()
            );
        """,
        )
        await _exec(
            conn,
            """CREATE INDEX IF NOT EXISTS "PrepMaterial_prepId_idx" ON "PrepMaterial"("prepId");""",
        )

        # QuizSession
        await _exec(
            conn,
            """
            CREATE TABLE IF NOT EXISTS "QuizSession" (
                id VARCHAR PRIMARY KEY,
                "userId" VARCHAR NOT NULL REFERENCES "User"(id) ON DELETE CASCADE,
                "prepId" VARCHAR NOT NULL REFERENCES "ExamPrep"(id) ON DELETE CASCADE,
                mode VARCHAR NOT NULL,
                "topicId" VARCHAR,
                status VARCHAR NOT NULL DEFAULT 'IN_PROGRESS',
                "totalQuestions" INTEGER NOT NULL DEFAULT 0,
                "correctCount" INTEGER NOT NULL DEFAULT 0,
                "scorePercentage" FLOAT,
                "durationSeconds" INTEGER,
                "completedAt" TIMESTAMPTZ,
                "createdAt" TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                "updatedAt" TIMESTAMPTZ NOT NULL DEFAULT NOW()
            );
        """,
        )
        await _exec(
            conn,
            """CREATE INDEX IF NOT EXISTS "QuizSession_userId_prepId_idx" ON "QuizSession"("userId", "prepId");""",
        )

        # QuizQuestion
        await _exec(
            conn,
            """
            CREATE TABLE IF NOT EXISTS "QuizQuestion" (
                id VARCHAR PRIMARY KEY,
                "quizSessionId" VARCHAR NOT NULL REFERENCES "QuizSession"(id) ON DELETE CASCADE,
                "prepTopicId" VARCHAR REFERENCES "PrepTopic"(id) ON DELETE SET NULL,
                "questionText" TEXT NOT NULL,
                "questionType" VARCHAR NOT NULL DEFAULT 'MULTIPLE_CHOICE',
                options JSONB,
                "correctAnswer" VARCHAR NOT NULL,
                explanation TEXT,
                "orderIndex" INTEGER NOT NULL DEFAULT 0,
                "createdAt" TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                "updatedAt" TIMESTAMPTZ NOT NULL DEFAULT NOW()
            );
        """,
        )
        await _exec(
            conn,
            """CREATE INDEX IF NOT EXISTS "QuizQuestion_quizSessionId_idx" ON "QuizQuestion"("quizSessionId");""",
        )

        # QuizAnswer
        await _exec(
            conn,
            """
            CREATE TABLE IF NOT EXISTS "QuizAnswer" (
                id VARCHAR PRIMARY KEY,
                "quizSessionId" VARCHAR NOT NULL REFERENCES "QuizSession"(id) ON DELETE CASCADE,
                "questionId" VARCHAR NOT NULL REFERENCES "QuizQuestion"(id) ON DELETE CASCADE,
                "userAnswer" VARCHAR NOT NULL,
                "isCorrect" BOOLEAN NOT NULL DEFAULT FALSE,
                "timeTakenSeconds" INTEGER,
                "createdAt" TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                "updatedAt" TIMESTAMPTZ NOT NULL DEFAULT NOW()
            );
        """,
        )
        await _exec(
            conn,
            """CREATE INDEX IF NOT EXISTS "QuizAnswer_quizSessionId_idx" ON "QuizAnswer"("quizSessionId");""",
        )

        # StudyPlan
        await _exec(
            conn,
            """
            CREATE TABLE IF NOT EXISTS "StudyPlan" (
                id VARCHAR PRIMARY KEY,
                "userId" VARCHAR NOT NULL REFERENCES "User"(id) ON DELETE CASCADE,
                title VARCHAR NOT NULL,
                "goalDescription" TEXT,
                deadline TIMESTAMPTZ NOT NULL,
                "prepId" VARCHAR REFERENCES "ExamPrep"(id) ON DELETE SET NULL,
                status VARCHAR NOT NULL DEFAULT 'ACTIVE',
                "totalItems" INTEGER NOT NULL DEFAULT 0,
                "completedItems" INTEGER NOT NULL DEFAULT 0,
                "createdAt" TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                "updatedAt" TIMESTAMPTZ NOT NULL DEFAULT NOW()
            );
        """,
        )
        await _exec(
            conn,
            """CREATE INDEX IF NOT EXISTS "StudyPlan_userId_status_idx" ON "StudyPlan"("userId", status);""",
        )

        # StudyPlanItem
        await _exec(
            conn,
            """
            CREATE TABLE IF NOT EXISTS "StudyPlanItem" (
                id VARCHAR PRIMARY KEY,
                "planId" VARCHAR NOT NULL REFERENCES "StudyPlan"(id) ON DELETE CASCADE,
                title VARCHAR NOT NULL,
                description TEXT,
                "scheduledDate" TIMESTAMPTZ NOT NULL,
                "estimatedMinutes" INTEGER NOT NULL DEFAULT 30,
                "itemType" VARCHAR NOT NULL DEFAULT 'STUDY',
                "topicId" VARCHAR,
                "prepTopicId" VARCHAR,
                status VARCHAR NOT NULL DEFAULT 'PENDING',
                "completedAt" TIMESTAMPTZ,
                "createdAt" TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                "updatedAt" TIMESTAMPTZ NOT NULL DEFAULT NOW()
            );
        """,
        )
        await _exec(
            conn,
            """CREATE INDEX IF NOT EXISTS "StudyPlanItem_planId_scheduledDate_idx" ON "StudyPlanItem"("planId", "scheduledDate");""",
        )

        # Reflection
        await _exec(
            conn,
            """
            CREATE TABLE IF NOT EXISTS "Reflection" (
                id VARCHAR PRIMARY KEY,
                "userId" VARCHAR NOT NULL REFERENCES "User"(id) ON DELETE CASCADE,
                type VARCHAR NOT NULL,
                "periodStart" TIMESTAMPTZ NOT NULL,
                "periodEnd" TIMESTAMPTZ NOT NULL,
                summary TEXT NOT NULL,
                "activitiesLayer" JSONB,
                "progressLayer" JSONB,
                "achievementsLayer" JSONB,
                recommendations JSONB,
                "createdAt" TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                "updatedAt" TIMESTAMPTZ NOT NULL DEFAULT NOW()
            );
        """,
        )
        await _exec(
            conn,
            """CREATE INDEX IF NOT EXISTS "Reflection_userId_type_idx" ON "Reflection"("userId", type);""",
        )
        await _exec(
            conn,
            """CREATE INDEX IF NOT EXISTS "Reflection_periodEnd_idx" ON "Reflection"("periodEnd");""",
        )

        # DiscoveryRecommendation
        await _exec(
            conn,
            """
            CREATE TABLE IF NOT EXISTS "DiscoveryRecommendation" (
                id VARCHAR PRIMARY KEY,
                "userId" VARCHAR NOT NULL REFERENCES "User"(id) ON DELETE CASCADE,
                "itemType" VARCHAR NOT NULL,
                "itemId" VARCHAR NOT NULL,
                title VARCHAR NOT NULL,
                reason VARCHAR NOT NULL,
                "relevanceScore" FLOAT NOT NULL DEFAULT 0.0,
                status VARCHAR NOT NULL DEFAULT 'ACTIVE',
                "dismissedAt" TIMESTAMPTZ,
                "followedAt" TIMESTAMPTZ,
                "createdAt" TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                "updatedAt" TIMESTAMPTZ NOT NULL DEFAULT NOW()
            );
        """,
        )
        await _exec(
            conn,
            """CREATE INDEX IF NOT EXISTS "DiscoveryRecommendation_userId_status_idx" ON "DiscoveryRecommendation"("userId", status);""",
        )

        # ActivityFeedEntry
        await _exec(
            conn,
            """
            CREATE TABLE IF NOT EXISTS "ActivityFeedEntry" (
                id VARCHAR PRIMARY KEY,
                "userId" VARCHAR NOT NULL REFERENCES "User"(id) ON DELETE CASCADE,
                "activityType" VARCHAR NOT NULL,
                title VARCHAR NOT NULL,
                description VARCHAR,
                context JSONB,
                "occurredAt" TIMESTAMPTZ NOT NULL
            );
        """,
        )
        await _exec(
            conn,
            """CREATE INDEX IF NOT EXISTS "ActivityFeedEntry_userId_occurredAt_idx" ON "ActivityFeedEntry"("userId", "occurredAt");""",
        )

        print("\n=== Phase 3: Stamp Alembic version ===\n")

        # Create alembic_version table if not exists and stamp to latest
        await _exec(
            conn,
            """
            CREATE TABLE IF NOT EXISTS alembic_version (
                version_num VARCHAR(32) NOT NULL,
                CONSTRAINT alembic_version_pkc PRIMARY KEY (version_num)
            );
        """,
        )
        await _exec(conn, """DELETE FROM alembic_version;""")
        await _exec(
            conn,
            """INSERT INTO alembic_version (version_num) VALUES ('003_add_personal_learning_models');""",
        )

        print("\n=== Schema sync complete! ===")
        print("Database is now aligned with SQLAlchemy models.")
        print("Alembic stamped at: 003_add_personal_learning_models")

    await engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())
