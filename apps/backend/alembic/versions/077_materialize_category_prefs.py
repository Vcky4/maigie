"""Materialize category-level notification preferences for every user.

Migration 059 normalized only *exact-type* preference rows and left category-level consent to be
computed on read from the legacy `UserPreferences` columns. That read fallback and the send-time
dispatch path disagree for any type without an exact row: the settings read synthesizes a value from
the legacy booleans, while the dispatcher fails closed on a missing row. This materializes the
category-level rows the settings read currently computes, so that (a) the legacy columns can stop
being read, and (b) dispatch consent is explicit for every (category, channel) rather than
fail-closed-by-omission.

This is a deliberate, signed-off behaviour change at the seam: a category's non-exact email-allowed
types — notably the weekly digests — begin to follow the category's email setting instead of being
silently suppressed for lack of a row. Its real-world effect is gated behind the email rollout
(`NOTIFICATION_EMAIL_ENABLED`), which is off, so no message changes today; the rows simply become the
source of truth ahead of that rollout.

Reproduces `notifications.service._effective_category_setting` exactly, as of this revision. The logic
is duplicated here rather than imported so the migration is a fixed point in time that a later change
to the service cannot silently alter. Idempotent: only inserts a category-level row where none exists
for that (user, category, channel), and uses a deterministic id, so a re-run is a no-op. Never
touches exact-type rows, and never writes WEB_PUSH (there was never a legacy column for browser
consent, so its honest state stays "not asked" — dispatch remains fail-closed for it).
"""

import hashlib

import sqlalchemy as sa

from alembic import op

revision = "077_materialize_category_prefs"
down_revision = "076_drop_notification_pushed_at"
branch_labels = None
depends_on = None

# settings category -> the database categories it expands to (mirrors service._SETTINGS_CATEGORIES).
_SETTINGS_CATEGORIES = {
    "LEARNING": ("LEARNING",),
    "PROGRESS": ("PROGRESS",),
    "SOCIAL_CLASSROOM": ("SOCIAL", "CLASSROOM"),
    "PRODUCT_UPDATES": ("OPERATIONS",),
}
_DEFAULT_IN_APP = {"LEARNING": True, "PROGRESS": True}


def _effective(key, prefs, legacy):
    """Reproduce service._effective_category_setting: return (in_app, mobile_push, email_frequency).

    `prefs` is the list of this user's preference rows as dicts with category/channel/
    notification_type/enabled/frequency. `legacy` is the user's UserPreferences dict or None.
    """
    categories = _SETTINGS_CATEGORIES[key]

    def rows(channel, *, exact):
        return [
            r
            for r in prefs
            if r["category"] in categories
            and r["channel"] == channel
            and (r["notification_type"] is not None) is exact
        ]

    def enabled(channel, default):
        cat_rows = rows(channel, exact=False)
        if cat_rows:
            return all(r["enabled"] and r["frequency"] == "IMMEDIATE" for r in cat_rows)
        exact_rows = rows(channel, exact=True)
        if exact_rows:
            return any(r["enabled"] and r["frequency"] == "IMMEDIATE" for r in exact_rows)
        return default

    email_rows = rows("EMAIL", exact=False) or rows("EMAIL", exact=True)
    if any(r["enabled"] and r["frequency"] == "DIGEST" for r in email_rows):
        email_frequency = "WEEKLY"
    elif any(r["enabled"] and r["frequency"] == "IMMEDIATE" for r in email_rows):
        email_frequency = "IMMEDIATE"
    else:
        email_frequency = "OFF"
    if not email_rows and legacy is not None:
        if key == "LEARNING" and legacy["emailScheduleReminder"]:
            email_frequency = "IMMEDIATE"
        elif key == "PROGRESS" and legacy["emailWeeklyTips"]:
            email_frequency = "WEEKLY"

    mobile_default = False
    if not rows("MOBILE_PUSH", exact=False) and not rows("MOBILE_PUSH", exact=True):
        if key == "LEARNING" and legacy is not None:
            mobile_default = bool(legacy["pushScheduleReminder"] or legacy["pushStudyTips"])

    return (
        enabled("IN_APP", _DEFAULT_IN_APP.get(key, False)),
        enabled("MOBILE_PUSH", mobile_default),
        email_frequency,
    )


def _pref_id(user_id: str, category: str, channel: str) -> str:
    # Deterministic so a re-run cannot create a duplicate even before the unique index is consulted.
    return hashlib.md5(f"notification-pref-cat|{user_id}|{category}|{channel}".encode()).hexdigest()


def upgrade() -> None:
    conn = op.get_bind()

    users = [r[0] for r in conn.execute(sa.text('SELECT id FROM "User"'))]

    prefs_by_user: dict[str, list[dict]] = {}
    for row in conn.execute(
        sa.text(
            'SELECT "userId", category, "notificationType", channel, enabled, frequency '
            'FROM "NotificationPreference"'
        )
    ):
        prefs_by_user.setdefault(row[0], []).append(
            {
                "category": row[1],
                "notification_type": row[2],
                "channel": row[3],
                "enabled": row[4],
                "frequency": row[5],
            }
        )

    legacy_by_user: dict[str, dict] = {}
    for row in conn.execute(
        sa.text(
            'SELECT "userId", "emailScheduleReminder", "emailWeeklyTips", '
            '"pushScheduleReminder", "pushStudyTips" FROM "UserPreferences"'
        )
    ):
        legacy_by_user[row[0]] = {
            "emailScheduleReminder": row[1],
            "emailWeeklyTips": row[2],
            "pushScheduleReminder": row[3],
            "pushStudyTips": row[4],
        }

    # Existing category-level rows (notificationType IS NULL) — never overwrite a saved choice.
    existing = {
        (row[0], row[1], row[2])
        for row in conn.execute(
            sa.text(
                'SELECT "userId", category, channel FROM "NotificationPreference" '
                'WHERE "notificationType" IS NULL'
            )
        )
    }

    insert = sa.text(
        """
        INSERT INTO "NotificationPreference"
            (id, "userId", category, "notificationType", channel, enabled, frequency, "digestPeriod")
        VALUES
            (:id, :user_id, :category, NULL, :channel, :enabled, :frequency, :digest_period)
        ON CONFLICT DO NOTHING
        """
    )

    batch: list[dict] = []
    for user_id in users:
        prefs = prefs_by_user.get(user_id, [])
        legacy = legacy_by_user.get(user_id)
        for key, db_categories in _SETTINGS_CATEGORIES.items():
            in_app, mobile_push, email_frequency = _effective(key, prefs, legacy)
            email_enabled = email_frequency != "OFF"
            email_freq = "DIGEST" if email_frequency == "WEEKLY" else email_frequency
            channel_rows = [
                ("IN_APP", in_app, "IMMEDIATE" if in_app else "OFF", None),
                ("MOBILE_PUSH", mobile_push, "IMMEDIATE" if mobile_push else "OFF", None),
                (
                    "EMAIL",
                    email_enabled,
                    email_freq,
                    "WEEKLY" if email_frequency == "WEEKLY" else None,
                ),
            ]
            for db_category in db_categories:
                for channel, enabled_v, frequency_v, digest_period in channel_rows:
                    if (user_id, db_category, channel) in existing:
                        continue
                    batch.append(
                        {
                            "id": _pref_id(user_id, db_category, channel),
                            "user_id": user_id,
                            "category": db_category,
                            "channel": channel,
                            "enabled": enabled_v,
                            "frequency": frequency_v,
                            "digest_period": digest_period,
                        }
                    )
        if len(batch) >= 1000:
            conn.execute(insert, batch)
            batch = []
    if batch:
        conn.execute(insert, batch)


def downgrade() -> None:
    # Remove only the category-level rows this migration could have created, identified by their
    # deterministic ids. Exact-type rows and any API-saved category rows (different ids) are untouched.
    conn = op.get_bind()
    ids = []
    for user_id in (r[0] for r in conn.execute(sa.text('SELECT id FROM "User"'))):
        for db_categories in _SETTINGS_CATEGORIES.values():
            for db_category in db_categories:
                for channel in ("IN_APP", "MOBILE_PUSH", "EMAIL"):
                    ids.append(_pref_id(user_id, db_category, channel))
    for i in range(0, len(ids), 1000):
        conn.execute(
            sa.text('DELETE FROM "NotificationPreference" WHERE id = ANY(:ids)'),
            {"ids": ids[i : i + 1000]},
        )
