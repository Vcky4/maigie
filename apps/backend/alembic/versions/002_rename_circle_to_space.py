"""Rename Circle → Space across all tables, columns, and indexes.

This migration renames all Circle-related database objects to use the
Learning Spaces terminology adopted in the product.

Tables renamed:
  Circle → Space
  CircleMember → SpaceMember
  CircleChatGroup → SpaceChatGroup
  CircleChatGroupMember → SpaceChatGroupMember
  CircleInvite → SpaceInvite
  CircleMemberStat → SpaceMemberStat
  CircleSession → SpaceSession
  CircleJoinRequest → SpaceJoinRequest
  CircleSubscription → SpaceSubscription
  CircleSeatAddon → SpaceSeatAddon

Columns renamed (across multiple tables):
  circleId → spaceId
  circlePlanActive → spacePlanActive
  circlePlanCurrentPeriodEnd → spacePlanCurrentPeriodEnd
  isCircleRoom → isSpaceRoom

Indexes renamed to match new table/column names.

Revision ID: 002_rename_circle_to_space
Revises: 001_drop_prisma_enums
Create Date: 2026-07-16
"""

import sqlalchemy as sa

from alembic import op

revision = "002_rename_circle_to_space"
down_revision = "001_drop_prisma_enums"
branch_labels = None
depends_on = None


# ---------------------------------------------------------------------------
# Table renames
# ---------------------------------------------------------------------------

TABLE_RENAMES = [
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

# ---------------------------------------------------------------------------
# Column renames: (table_NEW_name, old_column, new_column)
# Applied AFTER table renames.
# ---------------------------------------------------------------------------

COLUMN_RENAMES = [
    # Space table (was Circle)
    ("Space", "circlePlanActive", "spacePlanActive"),
    ("Space", "circlePlanCurrentPeriodEnd", "spacePlanCurrentPeriodEnd"),
    # SpaceMember (was CircleMember)
    ("SpaceMember", "circleId", "spaceId"),
    # SpaceChatGroup (was CircleChatGroup)
    ("SpaceChatGroup", "circleId", "spaceId"),
    # SpaceChatGroupMember (was CircleChatGroupMember) — no circleId column
    # SpaceInvite (was CircleInvite)
    ("SpaceInvite", "circleId", "spaceId"),
    # SpaceMemberStat (was CircleMemberStat)
    ("SpaceMemberStat", "circleId", "spaceId"),
    # SpaceSession (was CircleSession)
    ("SpaceSession", "circleId", "spaceId"),
    # SpaceJoinRequest (was CircleJoinRequest)
    ("SpaceJoinRequest", "circleId", "spaceId"),
    # SpaceSubscription (was CircleSubscription)
    ("SpaceSubscription", "circleId", "spaceId"),
    # SpaceSeatAddon (was CircleSeatAddon)
    ("SpaceSeatAddon", "circleId", "spaceId"),
    # AiUsageRecord
    ("AiUsageRecord", "circleId", "spaceId"),
    # ChatSession (intelligence domain)
    ("ChatSession", "circleId", "spaceId"),
    ("ChatSession", "isCircleRoom", "isSpaceRoom"),
    # Note (personal learning domain)
    ("Note", "circleId", "spaceId"),
    # Course (knowledge domain)
    ("Course", "circleId", "spaceId"),
    # Resource (knowledge domain)
    ("Resource", "circleId", "spaceId"),
    # Goal (progress domain)
    ("Goal", "circleId", "spaceId"),
    # LearningInsight (progress domain)
    ("LearningInsight", "circleId", "spaceId"),
]

# ---------------------------------------------------------------------------
# Index renames: (old_name, new_name)
# Applied AFTER column renames.
# ---------------------------------------------------------------------------

INDEX_RENAMES = [
    # Space table
    ("Circle_visibility_idx", "Space_visibility_idx"),
    ("Circle_featured_idx", "Space_featured_idx"),
    ("Circle_circlePlanActive_idx", "Space_spacePlanActive_idx"),
    # SpaceMember
    ("CircleMember_circleId_userId_key", "SpaceMember_spaceId_userId_key"),
    ("CircleMember_circleId_seatTier_idx", "SpaceMember_spaceId_seatTier_idx"),
    # SpaceChatGroupMember
    ("CircleChatGroupMember_chatGroupId_userId_key", "SpaceChatGroupMember_chatGroupId_userId_key"),
    # SpaceInvite
    ("CircleInvite_circleId_inviteeEmail_key", "SpaceInvite_spaceId_inviteeEmail_key"),
    # SpaceMemberStat
    ("CircleMemberStat_circleId_userId_key", "SpaceMemberStat_spaceId_userId_key"),
    # SpaceJoinRequest
    ("CircleJoinRequest_circleId_userId_key", "SpaceJoinRequest_spaceId_userId_key"),
    ("CircleJoinRequest_circleId_status_idx", "SpaceJoinRequest_spaceId_status_idx"),
    # SpaceSeatAddon
    ("CircleSeatAddon_circleId_status_idx", "SpaceSeatAddon_spaceId_status_idx"),
    ("CircleSeatAddon_circleId_assignedAt_idx", "SpaceSeatAddon_spaceId_assignedAt_idx"),
    # ChatSession
    ("ChatSession_isCircleRoom_idx", "ChatSession_isSpaceRoom_idx"),
    ("ChatSession_userId_circleId_courseId_idx", "ChatSession_userId_spaceId_courseId_idx"),
    ("ChatSession_userId_circleId_topicId_idx", "ChatSession_userId_spaceId_topicId_idx"),
    # AiUsageRecord
    ("AiUsageRecord_circleId_userId_idx", "AiUsageRecord_spaceId_userId_idx"),
    ("AiUsageRecord_circleId_createdAt_idx", "AiUsageRecord_spaceId_createdAt_idx"),
]

# ---------------------------------------------------------------------------
# Foreign key constraint renames (PostgreSQL auto-generates FK names)
# We drop and recreate FKs that reference the renamed Circle table.
# ---------------------------------------------------------------------------

# (table_new_name, column_new_name, old_fk_target_table, new_fk_target_table)
FK_UPDATES = [
    ("SpaceMember", "spaceId", "Space"),
    ("SpaceChatGroup", "spaceId", "Space"),
    ("SpaceChatGroupMember", "chatGroupId", "SpaceChatGroup"),
    ("SpaceInvite", "spaceId", "Space"),
    ("SpaceMemberStat", "spaceId", "Space"),
    ("SpaceSession", "spaceId", "Space"),
    ("SpaceJoinRequest", "spaceId", "Space"),
    ("SpaceSubscription", "spaceId", "Space"),
    ("SpaceSeatAddon", "spaceId", "Space"),
    ("Note", "spaceId", "Space"),
]


def upgrade() -> None:
    conn = op.get_bind()

    # Check if this is a fresh DB (no "Circle" table exists) — skip renames
    result = conn.execute(
        sa.text(
            "SELECT EXISTS (SELECT 1 FROM information_schema.tables WHERE table_name = 'Circle')"
        )
    )
    circle_exists = result.scalar()

    if not circle_exists:
        # Fresh database — tables were never named "Circle". Nothing to rename.
        return

    # 1. Rename tables
    for old_name, new_name in TABLE_RENAMES:
        op.rename_table(old_name, new_name)

    # 2. Rename columns
    for table, old_col, new_col in COLUMN_RENAMES:
        op.alter_column(table, old_col, new_column_name=new_col)

    # 3. Rename indexes (use raw SQL — Alembic has no rename_index)
    for old_name, new_name in INDEX_RENAMES:
        op.execute(f'ALTER INDEX IF EXISTS "{old_name}" RENAME TO "{new_name}"')

    # 4. Update foreign key constraints that referenced "Circle"."id"
    #    Drop old FK, create new FK pointing to "Space"."id"
    for table, col, ref_table in FK_UPDATES:
        # Find and drop existing FK constraint
        # PostgreSQL FK naming convention: {table}_{column}_fkey
        old_fk_name = f"{table}_{col}_fkey"
        op.execute(
            f"""
            DO $$
            BEGIN
                IF EXISTS (
                    SELECT 1 FROM information_schema.table_constraints
                    WHERE constraint_name = '{old_fk_name}'
                    AND table_name = '{table}'
                ) THEN
                    ALTER TABLE "{table}" DROP CONSTRAINT "{old_fk_name}";
                END IF;
            END $$;
        """
        )
        # Also try the old table/column naming pattern
        # Prisma uses: {OldTable}_{oldColumn}_fkey
        old_table = table  # already renamed
        op.execute(
            f"""
            DO $$
            DECLARE
                fk_name TEXT;
            BEGIN
                SELECT constraint_name INTO fk_name
                FROM information_schema.table_constraints tc
                JOIN information_schema.key_column_usage kcu
                    ON tc.constraint_name = kcu.constraint_name
                WHERE tc.table_name = '{table}'
                  AND kcu.column_name = '{col}'
                  AND tc.constraint_type = 'FOREIGN KEY'
                LIMIT 1;

                IF fk_name IS NOT NULL THEN
                    EXECUTE format('ALTER TABLE "%s" DROP CONSTRAINT "%s"', '{table}', fk_name);
                END IF;
            END $$;
        """
        )
        # Create new FK
        on_delete = "CASCADE" if table != "Note" else "SET NULL"
        nullable = "TRUE" if table == "Note" else "FALSE"
        op.create_foreign_key(
            f"{table}_{col}_fkey",
            table,
            ref_table,
            [col],
            ["id"],
            ondelete=on_delete,
        )


def downgrade() -> None:
    # Reverse FK updates
    for table, col, ref_table in reversed(FK_UPDATES):
        fk_name = f"{table}_{col}_fkey"
        op.execute(
            f"""
            DO $$
            DECLARE
                fk_name TEXT;
            BEGIN
                SELECT constraint_name INTO fk_name
                FROM information_schema.table_constraints tc
                JOIN information_schema.key_column_usage kcu
                    ON tc.constraint_name = kcu.constraint_name
                WHERE tc.table_name = '{table}'
                  AND kcu.column_name = '{col}'
                  AND tc.constraint_type = 'FOREIGN KEY'
                LIMIT 1;

                IF fk_name IS NOT NULL THEN
                    EXECUTE format('ALTER TABLE "%s" DROP CONSTRAINT "%s"', '{table}', fk_name);
                END IF;
            END $$;
        """
        )
        # Determine old table name for FK target
        old_ref = "Circle" if ref_table == "Space" else "CircleChatGroup"
        old_col = "circleId" if col == "spaceId" else col
        on_delete = "CASCADE" if table != "Note" else "SET NULL"
        op.create_foreign_key(
            f"{table}_{old_col}_fkey",
            table,
            old_ref,
            [col],
            ["id"],
            ondelete=on_delete,
        )

    # Reverse index renames
    for old_name, new_name in reversed(INDEX_RENAMES):
        op.execute(f'ALTER INDEX IF EXISTS "{new_name}" RENAME TO "{old_name}"')

    # Reverse column renames
    for table, old_col, new_col in reversed(COLUMN_RENAMES):
        op.alter_column(table, new_col, new_column_name=old_col)

    # Reverse table renames
    for old_name, new_name in reversed(TABLE_RENAMES):
        op.rename_table(new_name, old_name)
