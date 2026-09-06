"""Canonical in-app notification history and conservative legacy backfill."""

import sqlalchemy as sa

from alembic import op

revision = "060_notification_phase1"
down_revision = "059_normalized_notif_prefs"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_index(
        "Notification_userId_createdAt_id_idx",
        "Notification",
        ["userId", sa.text('"createdAt" DESC'), sa.text("id DESC")],
    )
    op.create_index(
        "Notification_active_unread_idx",
        "Notification",
        ["userId", "eligibleAt", "expiresAt"],
        postgresql_where=sa.text(
            '"readAt" IS NULL AND "dismissedAt" IS NULL AND "archivedAt" IS NULL'
        ),
    )

    # Universal fields are deterministic and do not reinterpret legacy intent.
    op.execute(
        sa.text(
            """
            UPDATE "Notification"
            SET "eligibleAt" = COALESCE("eligibleAt", "scheduledAt"),
                "idempotencyKey" = COALESCE("idempotencyKey", concat('legacy-', id)),
                "sourceDomain" = COALESCE("sourceDomain", 'legacy')
            WHERE "eligibleAt" IS NULL
               OR "idempotencyKey" IS NULL
               OR "sourceDomain" IS NULL
            """
        )
    )

    # Exact old type values with an unambiguous canonical meaning. Copy and
    # timing stay untouched; actionData remains the compatibility fallback.
    op.execute(
        sa.text(
            """
            UPDATE "Notification"
            SET type = CASE
                    WHEN type = 'DAILY_PLAN' THEN 'learning.morning_schedule'
                    WHEN type = 'ENGAGEMENT_NUDGE' THEN 'learning.momentum_support'
                    WHEN type = 'suggestion' AND "actionData"->>'topic_id' IS NOT NULL
                    THEN 'learning.resource_recommended'
                    WHEN type = 'preparation_review' AND "actionData"->>'prepId' IS NOT NULL
                    THEN 'learning.reflection_opportunity'
                    WHEN type = 'preparation_result' AND "actionData"->>'prepId' IS NOT NULL
                    THEN 'learning.reflection_opportunity'
                    WHEN type = 'study_plan_check_in' AND "actionData"->>'planId' IS NOT NULL
                    THEN 'learning.study_plan_checkin_reminder'
                    WHEN type = 'study_plan_redistributed' AND "actionData"->>'planId' IS NOT NULL
                    THEN 'learning.plan_redistributed'
                    WHEN type = 'goal_deadline_extended' AND "actionData"->>'goalId' IS NOT NULL
                    THEN 'learning.goal_deadline_changed'
                    WHEN type = 'goal_needs_decision' AND "actionData"->>'goalId' IS NOT NULL
                    THEN 'progress.goal_decision_required'
                    WHEN type = 'goal_at_risk' AND "actionData"->>'goalId' IS NOT NULL
                    THEN 'progress.goal_at_risk'
                    ELSE type
                END,
                category = CASE type
                    WHEN 'DAILY_PLAN' THEN 'LEARNING'
                    WHEN 'ENGAGEMENT_NUDGE' THEN 'LEARNING'
                    WHEN 'suggestion' THEN 'LEARNING'
                    WHEN 'preparation_review' THEN 'LEARNING'
                    WHEN 'preparation_result' THEN 'LEARNING'
                    WHEN 'study_plan_check_in' THEN 'LEARNING'
                    WHEN 'study_plan_redistributed' THEN 'LEARNING'
                    WHEN 'goal_deadline_extended' THEN 'LEARNING'
                    WHEN 'goal_needs_decision' THEN 'PROGRESS'
                    WHEN 'goal_at_risk' THEN 'PROGRESS'
                    ELSE category
                END,
                urgency = CASE type
                    WHEN 'goal_at_risk' THEN 'HIGH'
                    WHEN 'goal_needs_decision' THEN 'HIGH'
                    WHEN 'goal_deadline_extended' THEN 'HIGH'
                    WHEN 'suggestion' THEN 'LOW'
                    WHEN 'preparation_review' THEN 'LOW'
                    WHEN 'preparation_result' THEN 'LOW'
                    WHEN 'DAILY_PLAN' THEN 'LOW'
                    ELSE 'NORMAL'
                END,
                action = CASE
                    WHEN type IN ('goal_deadline_extended', 'goal_needs_decision', 'goal_at_risk')
                         AND "actionData"->>'goalId' IS NOT NULL
                    THEN json_build_object('version', 1, 'kind', 'OPEN_GOAL',
                                           'entityId', "actionData"->>'goalId')
                    WHEN type IN ('study_plan_check_in', 'study_plan_redistributed')
                         AND "actionData"->>'planId' IS NOT NULL
                    THEN json_build_object('version', 1, 'kind', 'OPEN_STUDY_PLAN',
                                           'entityId', "actionData"->>'planId')
                    WHEN type IN ('preparation_review', 'preparation_result')
                         AND "actionData"->>'prepId' IS NOT NULL
                    THEN json_build_object('version', 1, 'kind', 'OPEN_PREPARATION',
                                           'entityId', "actionData"->>'prepId')
                    WHEN type = 'suggestion' AND "actionData"->>'topic_id' IS NOT NULL
                    THEN json_build_object('version', 1, 'kind', 'OPEN_RESOURCE',
                                           'entityId', "actionData"->>'topic_id',
                                           'resourceType', 'TOPIC')
                    WHEN type IN ('DAILY_PLAN', 'ENGAGEMENT_NUDGE')
                    THEN json_build_object('version', 1, 'kind', 'OPEN_HOME')
                    ELSE action
                END,
                "sourceEntityType" = COALESCE("sourceEntityType", CASE
                    WHEN type IN ('goal_deadline_extended', 'goal_needs_decision', 'goal_at_risk')
                    THEN 'goal'
                    WHEN type IN ('study_plan_check_in', 'study_plan_redistributed') THEN 'study_plan'
                    WHEN type IN ('preparation_review', 'preparation_result') THEN 'preparation'
                    WHEN type = 'suggestion' THEN 'topic'
                    ELSE NULL
                END),
                "sourceEntityId" = COALESCE("sourceEntityId", CASE
                    WHEN type IN ('goal_deadline_extended', 'goal_needs_decision', 'goal_at_risk')
                    THEN "actionData"->>'goalId'
                    WHEN type IN ('study_plan_check_in', 'study_plan_redistributed')
                    THEN "actionData"->>'planId'
                    WHEN type IN ('preparation_review', 'preparation_result')
                    THEN "actionData"->>'prepId'
                    WHEN type = 'suggestion' THEN "actionData"->>'topic_id'
                    ELSE NULL
                END),
                "expiresAt" = COALESCE("expiresAt", "scheduledAt" + CASE type
                    WHEN 'DAILY_PLAN' THEN interval '1 day'
                    WHEN 'ENGAGEMENT_NUDGE' THEN interval '1 day'
                    WHEN 'suggestion' THEN interval '7 days'
                    WHEN 'preparation_review' THEN interval '3 days'
                    WHEN 'preparation_result' THEN interval '3 days'
                    WHEN 'study_plan_check_in' THEN interval '7 days'
                    WHEN 'study_plan_redistributed' THEN interval '7 days'
                    WHEN 'goal_deadline_extended' THEN interval '7 days'
                    WHEN 'goal_needs_decision' THEN interval '7 days'
                    WHEN 'goal_at_risk' THEN interval '3 days'
                    ELSE NULL
                END)
            WHERE type IN ('DAILY_PLAN', 'ENGAGEMENT_NUDGE')
               OR (type = 'suggestion' AND "actionData"->>'topic_id' IS NOT NULL)
               OR (type IN ('preparation_review', 'preparation_result')
                   AND "actionData"->>'prepId' IS NOT NULL)
               OR (type IN ('study_plan_check_in', 'study_plan_redistributed')
                   AND "actionData"->>'planId' IS NOT NULL)
               OR (type IN ('goal_deadline_extended', 'goal_needs_decision', 'goal_at_risk')
                   AND "actionData"->>'goalId' IS NOT NULL)
            """
        )
    )

    # Celebration was shared by streaks and achievements. Only the two exact
    # copy prefixes emitted by current producers are safe to classify.
    op.execute(
        sa.text(
            """
            UPDATE "Notification"
            SET type = CASE
                    WHEN title LIKE '🔥 %day streak!' THEN 'progress.activity_milestone'
                    WHEN title LIKE '🏆 %' THEN 'progress.achievement_earned'
                    ELSE type
                END,
                category = CASE
                    WHEN title LIKE '🔥 %day streak!' OR title LIKE '🏆 %' THEN 'PROGRESS'
                    ELSE category
                END,
                urgency = CASE
                    WHEN title LIKE '🔥 %day streak!' OR title LIKE '🏆 %' THEN 'LOW'
                    ELSE urgency
                END,
                action = CASE
                    WHEN title LIKE '🔥 %day streak!' OR title LIKE '🏆 %'
                    THEN json_build_object('version', 1, 'kind', 'OPEN_PROGRESS')
                    ELSE action
                END,
                "expiresAt" = CASE
                    WHEN "expiresAt" IS NOT NULL THEN "expiresAt"
                    WHEN title LIKE '🔥 %day streak!' THEN "scheduledAt" + interval '7 days'
                    WHEN title LIKE '🏆 %' THEN "scheduledAt" + interval '30 days'
                    ELSE NULL
                END
            WHERE type = 'celebration'
            """
        )
    )


def downgrade() -> None:
    # Backfilled values are intentionally retained: reverting runtime indexes
    # must not destroy canonical evidence or attempt to reconstruct ambiguity.
    op.drop_index("Notification_active_unread_idx", table_name="Notification")
    op.drop_index("Notification_userId_createdAt_id_idx", table_name="Notification")
