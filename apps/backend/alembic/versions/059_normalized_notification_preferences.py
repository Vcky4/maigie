"""Normalize notification policy and exact legacy channel preferences.

The new tables are shadow persistence only: legacy fields remain in place and
current readers/writers are untouched. True legacy values are copied only for
behaviors they already governed; no category-wide or web-push consent is inferred.
"""

import sqlalchemy as sa

from alembic import op

revision = "059_normalized_notif_prefs"
down_revision = "058_notification_foundation"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "NotificationPolicy",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("userId", sa.String(), nullable=False),
        sa.Column("engagementEnabled", sa.Boolean(), server_default="false", nullable=False),
        sa.Column("timezone", sa.String(), server_default="UTC", nullable=False),
        sa.Column("timezoneSource", sa.String(length=16), nullable=True),
        sa.Column("timezoneCapturedAt", sa.DateTime(timezone=True), nullable=True),
        sa.Column("language", sa.String(), server_default="en", nullable=False),
        sa.Column("quietHoursStart", sa.String(length=5), nullable=True),
        sa.Column("quietHoursEnd", sa.String(length=5), nullable=True),
        sa.Column("maxDailyNotifications", sa.Integer(), server_default="5", nullable=False),
        sa.Column("digestLocalTime", sa.String(length=5), nullable=True),
        sa.Column("digestDayOfWeek", sa.Integer(), nullable=True),
        sa.Column(
            "createdAt",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "updatedAt",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.CheckConstraint(
            "\"timezoneSource\" IS NULL OR \"timezoneSource\" IN ('DEVICE', 'MANUAL')",
            name="NotificationPolicy_timezoneSource_check",
        ),
        sa.CheckConstraint(
            '("quietHoursStart" IS NULL) = ("quietHoursEnd" IS NULL)',
            name="NotificationPolicy_quietHours_pair_check",
        ),
        sa.CheckConstraint(
            '"maxDailyNotifications" >= 1',
            name="NotificationPolicy_maxDailyNotifications_check",
        ),
        sa.CheckConstraint(
            '"digestDayOfWeek" IS NULL OR "digestDayOfWeek" BETWEEN 0 AND 6',
            name="NotificationPolicy_digestDayOfWeek_check",
        ),
        sa.CheckConstraint(
            '"digestDayOfWeek" IS NULL OR "digestLocalTime" IS NOT NULL',
            name="NotificationPolicy_digest_schedule_check",
        ),
        sa.ForeignKeyConstraint(["userId"], ["User.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("userId", name="NotificationPolicy_userId_key"),
    )

    op.create_table(
        "NotificationPreference",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("userId", sa.String(), nullable=False),
        sa.Column("category", sa.String(length=16), nullable=False),
        sa.Column("notificationType", sa.String(), nullable=True),
        sa.Column("channel", sa.String(length=16), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False),
        sa.Column("frequency", sa.String(length=16), nullable=False),
        sa.Column("digestPeriod", sa.String(length=16), nullable=True),
        sa.Column(
            "createdAt",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "updatedAt",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.CheckConstraint(
            "category IN ('SECURITY', 'ACCOUNT', 'BILLING', 'MEMBERSHIP', "
            "'SOCIAL', 'CLASSROOM', 'LEARNING', 'PROGRESS', 'SUPPORT', 'OPERATIONS')",
            name="NotificationPreference_category_check",
        ),
        sa.CheckConstraint(
            "channel IN ('IN_APP', 'MOBILE_PUSH', 'WEB_PUSH', 'EMAIL')",
            name="NotificationPreference_channel_check",
        ),
        sa.CheckConstraint(
            "frequency IN ('IMMEDIATE', 'DIGEST', 'OFF')",
            name="NotificationPreference_frequency_check",
        ),
        sa.CheckConstraint(
            "enabled = (frequency <> 'OFF')",
            name="NotificationPreference_enabled_frequency_check",
        ),
        sa.CheckConstraint(
            "(frequency = 'DIGEST' AND \"digestPeriod\" IS NOT NULL) OR "
            "(frequency <> 'DIGEST' AND \"digestPeriod\" IS NULL)",
            name="NotificationPreference_digest_check",
        ),
        sa.CheckConstraint(
            "\"digestPeriod\" IS NULL OR \"digestPeriod\" IN ('DAILY', 'WEEKLY')",
            name="NotificationPreference_digestPeriod_check",
        ),
        sa.CheckConstraint(
            '"notificationType" IS NULL OR length("notificationType") > 0',
            name="NotificationPreference_notificationType_check",
        ),
        sa.ForeignKeyConstraint(["userId"], ["User.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "NotificationPreference_user_category_channel_key",
        "NotificationPreference",
        ["userId", "category", "channel"],
        unique=True,
        postgresql_where=sa.text('"notificationType" IS NULL'),
    )
    op.create_index(
        "NotificationPreference_user_type_channel_key",
        "NotificationPreference",
        ["userId", "notificationType", "channel"],
        unique=True,
        postgresql_where=sa.text('"notificationType" IS NOT NULL'),
    )
    op.create_index(
        "NotificationPreference_userId_channel_category_idx",
        "NotificationPreference",
        ["userId", "channel", "category"],
    )

    # Every user gets a policy snapshot. Missing UserPreferences fails closed for
    # engagement, and a default UTC with no source remains explicitly unknown.
    op.execute(
        sa.text(
            """
            INSERT INTO "NotificationPolicy" (
                id, "userId", "engagementEnabled", timezone, "timezoneSource",
                "timezoneCapturedAt", language, "quietHoursStart", "quietHoursEnd",
                "maxDailyNotifications"
            )
            SELECT
                md5('notification-policy:' || u.id),
                u.id,
                COALESCE(up.notifications, false),
                COALESCE(up.timezone, 'UTC'),
                up."timezoneSource",
                up."timezoneCapturedAt",
                COALESCE(up.language, 'en'),
                lp."quietHoursStart",
                lp."quietHoursEnd",
                COALESCE(lp."maxDailyNotifications", 5)
            FROM "User" AS u
            LEFT JOIN "UserPreferences" AS up ON up."userId" = u.id
            LEFT JOIN "LearningProfile" AS lp ON lp."userId" = u.id
            """
        )
    )

    # Existing schedule-reminder email behavior: copy the exact true/false value.
    op.execute(
        sa.text(
            """
            INSERT INTO "NotificationPreference" (
                id, "userId", category, "notificationType", channel,
                enabled, frequency, "digestPeriod"
            )
            SELECT
                md5('notification-pref|' || up."userId" ||
                    '|learning.study_session_reminder|EMAIL'),
                up."userId", 'LEARNING', 'learning.study_session_reminder', 'EMAIL',
                up."emailScheduleReminder",
                CASE WHEN up."emailScheduleReminder" THEN 'IMMEDIATE' ELSE 'OFF' END,
                NULL
            FROM "UserPreferences" AS up
            """
        )
    )

    # WeeklyTips currently means the weekly progress summary, not all learning tips.
    op.execute(
        sa.text(
            """
            INSERT INTO "NotificationPreference" (
                id, "userId", category, "notificationType", channel,
                enabled, frequency, "digestPeriod"
            )
            SELECT
                md5('notification-pref|' || up."userId" || '|progress.weekly_summary|EMAIL'),
                up."userId", 'PROGRESS', 'progress.weekly_summary', 'EMAIL',
                up."emailWeeklyTips",
                CASE WHEN up."emailWeeklyTips" THEN 'DIGEST' ELSE 'OFF' END,
                CASE WHEN up."emailWeeklyTips" THEN 'WEEKLY' ELSE NULL END
            FROM "UserPreferences" AS up
            """
        )
    )

    # MorningSchedule has never had a reader. Preserve explicit false only; its
    # historical default true is not evidence of consent for a new sender.
    op.execute(
        sa.text(
            """
            INSERT INTO "NotificationPreference" (
                id, "userId", category, "notificationType", channel,
                enabled, frequency, "digestPeriod"
            )
            SELECT
                md5('notification-pref|' || up."userId" || '|learning.morning_schedule|EMAIL'),
                up."userId", 'LEARNING', 'learning.morning_schedule', 'EMAIL',
                false, 'OFF', NULL
            FROM "UserPreferences" AS up
            WHERE up."emailMorningSchedule" = false
            """
        )
    )

    # Copy pushScheduleReminder only to the three behaviors it currently gates.
    op.execute(
        sa.text(
            """
            INSERT INTO "NotificationPreference" (
                id, "userId", category, "notificationType", channel,
                enabled, frequency, "digestPeriod"
            )
            SELECT
                md5('notification-pref|' || up."userId" || '|' || mapped.type || '|MOBILE_PUSH'),
                up."userId", 'LEARNING', mapped.type, 'MOBILE_PUSH',
                up."pushScheduleReminder",
                CASE WHEN up."pushScheduleReminder" THEN 'IMMEDIATE' ELSE 'OFF' END,
                NULL
            FROM "UserPreferences" AS up
            CROSS JOIN (
                VALUES
                    ('learning.next_best_action'),
                    ('learning.study_plan_checkin_reminder'),
                    ('learning.plan_redistributed')
            ) AS mapped(type)
            """
        )
    )

    # Copy pushStudyTips only to the two legacy behaviors it currently gates.
    op.execute(
        sa.text(
            """
            INSERT INTO "NotificationPreference" (
                id, "userId", category, "notificationType", channel,
                enabled, frequency, "digestPeriod"
            )
            SELECT
                md5('notification-pref|' || up."userId" || '|' || mapped.type || '|MOBILE_PUSH'),
                up."userId", 'LEARNING', mapped.type, 'MOBILE_PUSH',
                up."pushStudyTips",
                CASE WHEN up."pushStudyTips" THEN 'IMMEDIATE' ELSE 'OFF' END,
                NULL
            FROM "UserPreferences" AS up
            CROSS JOIN (
                VALUES
                    ('learning.momentum_support'),
                    ('learning.resource_recommended')
            ) AS mapped(type)
            """
        )
    )


def downgrade() -> None:
    op.drop_index(
        "NotificationPreference_userId_channel_category_idx",
        table_name="NotificationPreference",
    )
    op.drop_index(
        "NotificationPreference_user_type_channel_key",
        table_name="NotificationPreference",
    )
    op.drop_index(
        "NotificationPreference_user_category_channel_key",
        table_name="NotificationPreference",
    )
    op.drop_table("NotificationPreference")
    op.drop_table("NotificationPolicy")
