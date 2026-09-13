"""Admin re-engagement analytics + recovery tools.

Analytics are computed from real `Notification` + `NotificationInteraction` rows: a "nudge" is an
engagement notification (mapped below), "acted on" is an `ACTIONED` interaction. "Came back within 48h"
has no reliable backing (no per-nudge return-attribution), so it is 0 rather than invented — the
honesty invariant. Bulk regeneration uses the non-LLM plan **redistribution** path (repacking drifted
plans), not a full LLM regen, so an admin click cannot run up cost or time out.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from sqlalchemy import func, select, text

from src.shared.database import get_session_factory

# Notification type -> the page's re-engagement category. Types not listed are not "nudges".
_TYPE_MAP: dict[str, str] = {
    "learning.gentle_return": "reengagement",
    "ENGAGEMENT_NUDGE": "reengagement",
    "learning.study_session_reminder": "study_gap",
    "learning.morning_schedule": "study_gap",
    "progress.goal_at_risk": "goal_nudge",
    "learning.goal_checkin_reminder": "goal_nudge",
    "progress.goal_decision_required": "goal_nudge",
    "learning.revision_reminder": "review_reminder",
    "learning.review_due": "review_reminder",
}

_DEEP_WAKE_KEY = "deepWake.maxInactiveDays"
_DEEP_WAKE_DEFAULT = 30


async def reengagement_analytics(days: int) -> dict:
    from src.domains.identity.db_models import User
    from src.domains.notifications.db_models import (
        Notification,
        NotificationInteraction,
    )

    now = datetime.now(UTC)
    since = now - timedelta(days=days)
    types = list(_TYPE_MAP.keys())

    factory = get_session_factory()
    async with factory() as session:
        base = (Notification.type.in_(types), Notification.created_at >= since)

        # ACTIONED notification ids, for "acted on".
        actioned_col = Notification.id.in_(
            select(NotificationInteraction.notification_id).where(
                NotificationInteraction.event == "ACTIONED"
            )
        )

        # By type: sent + acted-on per notification type.
        sent_by_type = dict(
            (
                await session.execute(
                    select(Notification.type, func.count()).where(*base).group_by(Notification.type)
                )
            ).all()
        )
        acted_by_type = dict(
            (
                await session.execute(
                    select(Notification.type, func.count())
                    .where(*base, actioned_col)
                    .group_by(Notification.type)
                )
            ).all()
        )

        # Daily sent + acted-on.
        day = func.date_trunc("day", Notification.created_at)
        sent_by_day = dict(
            (await session.execute(select(day, func.count()).where(*base).group_by(day))).all()
        )
        acted_day = func.date_trunc("day", Notification.created_at)
        acted_by_day = dict(
            (
                await session.execute(
                    select(acted_day, func.count()).where(*base, actioned_col).group_by(acted_day)
                )
            ).all()
        )

        # Recent 50, with the learner joined.
        recent_rows = (
            await session.execute(
                select(Notification, User.email, User.name)
                .outerjoin(User, User.id == Notification.user_id)
                .where(*base)
                .order_by(Notification.created_at.desc())
                .limit(50)
            )
        ).all()
        recent_ids = [n.id for n, _e, _nm in recent_rows]
        actioned_ids: set[str] = set()
        if recent_ids:
            actioned_ids = {
                r
                for (r,) in (
                    await session.execute(
                        select(NotificationInteraction.notification_id).where(
                            NotificationInteraction.notification_id.in_(recent_ids),
                            NotificationInteraction.event == "ACTIONED",
                        )
                    )
                ).all()
            }

    # --- Aggregate into the page's shape ---
    by_category: dict[str, dict] = {}
    for ntype, sent in sent_by_type.items():
        cat = _TYPE_MAP.get(ntype, "reengagement")
        bucket = by_category.setdefault(cat, {"sent": 0, "actedOn": 0})
        bucket["sent"] += int(sent)
    for ntype, acted in acted_by_type.items():
        cat = _TYPE_MAP.get(ntype, "reengagement")
        bucket = by_category.setdefault(cat, {"sent": 0, "actedOn": 0})
        bucket["actedOn"] += int(acted)
    for bucket in by_category.values():
        bucket["effectivenessRate"] = (
            round(bucket["actedOn"] / bucket["sent"] * 100) if bucket["sent"] else 0
        )

    total_sent = sum(int(v) for v in sent_by_type.values())
    total_acted = sum(int(v) for v in acted_by_type.values())

    sent_days = {d.date().isoformat(): int(c) for d, c in sent_by_day.items() if d is not None}
    acted_days = {d.date().isoformat(): int(c) for d, c in acted_by_day.items() if d is not None}
    daily = []
    for i in range(days):
        key = (since + timedelta(days=i + 1)).date().isoformat()
        daily.append(
            {
                "date": key,
                "sent": sent_days.get(key, 0),
                "actedOn": acted_days.get(key, 0),
            }
        )

    recent_log = []
    for notif, email, name in recent_rows:
        if notif.dismissed_at is not None:
            status = "dismissed"
        elif notif.id in actioned_ids:
            status = "acted_on"
        else:
            status = "sent"
        recent_log.append(
            {
                "id": notif.id,
                "type": _TYPE_MAP.get(notif.type, "reengagement"),
                "userName": name,
                "userEmail": email,
                "title": notif.title,
                "status": status,
                "automated": (notif.source_domain or "") != "admin",
                "createdAt": notif.created_at.isoformat() if notif.created_at else None,
            }
        )

    return {
        "summary": {
            "totalSent": total_sent,
            "totalActedOn": total_acted,
            "overallEffectiveness": (round(total_acted / total_sent * 100) if total_sent else 0),
            # No per-nudge return attribution exists, so this is not measured rather than invented.
            "comebackCount": 0,
            "comebackRate": 0,
        },
        "byType": by_category,
        "dailyData": daily,
        "recentLog": recent_log,
    }


async def deep_wake_config() -> dict:
    """Read the deep-wake threshold from `SystemConfig` (defaults to 30 days)."""
    factory = get_session_factory()
    async with factory() as session:
        row = (
            await session.execute(
                text('SELECT value FROM "SystemConfig" WHERE key = :k'),
                {"k": _DEEP_WAKE_KEY},
            )
        ).first()
    value = _DEEP_WAKE_DEFAULT
    if row and str(row[0]).isdigit():
        value = int(row[0])
    return {
        "max_inactive_days": value,
        "description": (
            "Learners inactive up to this many days are eligible for an automated deep-wake nudge."
        ),
    }


async def set_deep_wake_config(max_inactive_days: int) -> dict:
    """Persist the deep-wake threshold to `SystemConfig` (upsert on the key)."""
    import uuid

    factory = get_session_factory()
    async with factory() as session:
        existing = (
            await session.execute(
                text('SELECT id FROM "SystemConfig" WHERE key = :k'),
                {"k": _DEEP_WAKE_KEY},
            )
        ).first()
        now = datetime.now(UTC).replace(tzinfo=None)
        if existing:
            await session.execute(
                text('UPDATE "SystemConfig" SET value = :v, "updatedAt" = :t WHERE key = :k'),
                {"v": str(max_inactive_days), "t": now, "k": _DEEP_WAKE_KEY},
            )
        else:
            await session.execute(
                text(
                    'INSERT INTO "SystemConfig" (id, key, value, category, label, "createdAt", '
                    '"updatedAt") VALUES (:id, :k, :v, :cat, :label, :t, :t)'
                ),
                {
                    "id": uuid.uuid4().hex[:25],
                    "k": _DEEP_WAKE_KEY,
                    "v": str(max_inactive_days),
                    "cat": "retention",
                    "label": "Deep wake max inactive days",
                    "t": now,
                },
            )
        await session.commit()
    return await deep_wake_config()


async def bulk_regenerate_schedules(max_users: int, only_inactive_days: int | None) -> dict:
    """Repack drifted study plans so returning learners see fresh ones.

    Uses the non-LLM redistribution path (`study_plan_service.redistribute_drifted_plans`), which
    repacks plans that have drifted and are off cooldown — safe to trigger from a request, unlike a
    full LLM regen. Returns how many plans were moved.
    """
    from src.domains.personal_learning.services import study_plan_service

    moved = await study_plan_service.redistribute_drifted_plans(limit=max(1, min(max_users, 500)))
    return {"total": moved, "regenerated": moved, "failed": 0, "skipped": 0}
