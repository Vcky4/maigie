"""Send one real web push to a learner's browser, and explain it if nothing arrives.

    python scripts/db_direct.py python scripts/send_test_web_push.py --email you@example.com
    python scripts/db_direct.py python scripts/send_test_web_push.py --email you@example.com --check
    python scripts/db_direct.py python scripts/send_test_web_push.py --email you@example.com --dispatch-only

This exists because "no notification appeared" has about eight causes, and the browser shows you
none of them. A push has to clear the kill switch, VAPID configuration, the rollout cohort, the
engagement master switch, the legacy master switch, a per-category consent row, quiet hours, and
a live subscription — and then the push service still has to accept it. So this reports the state
of every gate before sending, then sends through the real dispatcher and reports what the ledger
recorded.

It deliberately does **not** bypass consent. A test that grants itself permission proves nothing
about the path a real notification takes.
"""

from __future__ import annotations

import argparse
import asyncio
from datetime import UTC, datetime

from sqlalchemy import select, text

TEST_TYPE = "learning.study_session_reminder"


def _tick(value: bool) -> str:
    return "yes" if value else "NO"


async def _resolve_user(email: str | None, user_id: str | None) -> tuple[str, str]:
    from src.shared.database.session import get_session_factory

    factory = get_session_factory()
    async with factory() as session:
        if user_id:
            row = (
                await session.execute(
                    text('SELECT id, email FROM "User" WHERE id = :v'), {"v": user_id}
                )
            ).first()
        else:
            row = (
                await session.execute(
                    text('SELECT id, email FROM "User" WHERE lower(email) = lower(:v)'),
                    {"v": email},
                )
            ).first()
    if row is None:
        raise SystemExit(f"No user matched {user_id or email!r}")
    return row[0], row[1]


async def report_gates(user_id: str) -> bool:
    """Print every gate between a notification and this learner's browser."""

    from src.config import get_settings
    from src.domains.notifications.db_models import PushInstallation
    from src.domains.notifications.feature_flags import capability_gate, stable_user_cohort
    from src.domains.notifications.repository import notification_repo
    from src.domains.notifications.web_push_delivery import web_push_configured
    from src.shared.database.session import get_session_factory

    settings = get_settings()
    gate = capability_gate("WEB_PUSH")
    cohort = stable_user_cohort(user_id)
    allowlisted = user_id in gate.allowlist or user_id in gate.internal_allowlist
    in_cohort = allowlisted or cohort < gate.rollout_percent
    configured = web_push_configured()

    print("--- sender ---")
    print(f"  WEB_PUSH_ENABLED            {_tick(gate.enabled)}")
    print(f"  VAPID keys usable           {_tick(configured)}")
    print(f"  VAPID subject               {settings.WEB_PUSH_VAPID_SUBJECT or '(unset)'}")
    print(
        f"  public key                  {(settings.WEB_PUSH_VAPID_PUBLIC_KEY or '(unset)')[:24]}…"
    )
    print(
        f"  in rollout                  {_tick(in_cohort)}"
        f"  (bucket {cohort}, rollout {gate.rollout_percent}%"
        f"{', allowlisted' if allowlisted else ''})"
    )
    print(f"  denylisted                  {_tick(user_id in gate.denylist)}")

    factory = get_session_factory()
    async with factory() as session:
        installations = list(
            (
                await session.execute(
                    select(PushInstallation).where(
                        PushInstallation.user_id == user_id,
                        PushInstallation.transport == "WEB_PUSH",
                    )
                )
            ).scalars()
        )
        consent = (
            await session.execute(
                text(
                    'SELECT category, enabled, frequency FROM "NotificationPreference" '
                    "WHERE \"userId\" = :u AND channel = 'WEB_PUSH' "
                    'AND "notificationType" IS NULL ORDER BY category'
                ),
                {"u": user_id},
            )
        ).all()

    print("--- this learner's browsers ---")
    if not installations:
        print("  none registered — open Settings › Notifications and turn browser push on")
    for row in installations:
        live = row.disabled_at is None and row.endpoint is not None
        host = (row.endpoint or "").split("/")[2] if row.endpoint else "(released)"
        print(
            f"  {'live   ' if live else 'retired'} {row.installation_id[:18]:20s} {host:34s}"
            f" permission={row.permission_state} failures={row.failure_count}"
        )

    # A subscription's secrets are encrypted under the SECRET_KEY of whichever deployment stored
    # them. A process with a different SECRET_KEY cannot decrypt them, and the dispatcher correctly
    # reads that as unusable key material and prunes the subscription — so running this script
    # against a subscription created by another environment destroys it. Right for a genuine key
    # rotation, wrong here, so it is checked before anything is sent.
    from src.domains.notifications.subscription_crypto import (
        SubscriptionSecretUnreadable,
        decrypt_subscription_secret,
    )

    print("--- can this process decrypt these subscriptions? ---")
    unreadable = 0
    for row in installations:
        if row.disabled_at is not None or not row.p256dh_encrypted:
            continue
        try:
            decrypt_subscription_secret(row.p256dh_encrypted)
            print(f"  readable    {row.installation_id[:18]}")
        except SubscriptionSecretUnreadable:
            unreadable += 1
            print(
                f"  UNREADABLE  {row.installation_id[:18]}  stored by a deployment with a "
                "different SECRET_KEY"
            )
    if unreadable:
        print("  Sending from here would prune it. Dispatch from that deployment instead.")

    print("--- consent ---")
    decision = await notification_repo.channel_policy(user_id, TEST_TYPE, "LEARNING", "WEB_PUSH")
    policy = decision["policy"]
    legacy = decision["legacy"]
    override = decision["override"]
    print(f"  engagement enabled          {_tick(bool(policy and policy.engagement_enabled))}")
    print(f"  legacy master on            {_tick(bool(legacy and legacy.notifications))}")
    print(
        f"  LEARNING browser consent    "
        f"{_tick(bool(override and override.enabled))}"
        f"{'' if override else '  (no row — nothing was saved for this category)'}"
    )
    if override:
        print(f"  frequency                   {override.frequency}")
    if policy:
        print(
            f"  quiet hours                 "
            f"{policy.quiet_hours_start or '—'} to {policy.quiet_hours_end or '—'}"
            f" ({policy.timezone})"
        )
    for category, enabled, frequency in consent:
        print(f"    {category:12s} enabled={enabled} frequency={frequency}")

    live = [row for row in installations if row.disabled_at is None and row.endpoint is not None]
    return bool(live) and gate.enabled and configured and in_cohort and not unreadable


async def create_notification(user_id: str) -> str:
    from src.domains.notifications import service

    row = await service.create_notification(
        user_id=user_id,
        type=TEST_TYPE,
        title="Test push from Maigie",
        body="If you can read this, web push works end to end.",
        action={"kind": "OPEN_SESSION", "entityId": "web-push-gate-check"},
        # Unique per run, so a repeat is a new notification rather than a replayed one.
        idempotency_key=f"web-push-gate:{datetime.now(UTC).isoformat()}",
        priority=2,
    )
    print(f"\ncreated notification {row.id} ({row.status}, eligible {row.eligible_at})")
    return row.id


async def bring_forward(user_id: str, notification_id: str | None) -> None:
    """Make a planned web push due now, for testing only.

    The orchestrator defers a notification past the learner's daily attention budget, which is
    correct behaviour and inconvenient when you are trying to observe one send. This moves the
    schedule and nothing else: the dispatcher still rechecks the kill switch, VAPID configuration,
    cohort, engagement and legacy switches, per-category consent, and quiet hours. It cannot make
    a push happen that policy would refuse — it only stops you waiting for the clock.
    """

    from sqlalchemy import text

    from src.shared.database.session import get_session_factory

    now = datetime.now(UTC)
    factory = get_session_factory()
    async with factory() as session, session.begin():
        if notification_id:
            await session.execute(
                text('UPDATE "Notification" SET "eligibleAt" = :n WHERE id = :i'),
                {"n": now, "i": notification_id},
            )
            result = await session.execute(
                text(
                    'UPDATE "NotificationDelivery" SET "eligibleAt" = :n, "nextAttemptAt" = :n '
                    "WHERE \"notificationId\" = :i AND channel = 'WEB_PUSH' "
                    "AND status IN ('PLANNED', 'QUEUED')"
                ),
                {"n": now, "i": notification_id},
            )
        else:
            result = await session.execute(
                text(
                    'UPDATE "NotificationDelivery" SET "eligibleAt" = :n, "nextAttemptAt" = :n '
                    "WHERE \"userId\" = :u AND channel = 'WEB_PUSH' "
                    "AND status IN ('PLANNED', 'QUEUED')"
                ),
                {"n": now, "u": user_id},
            )
    print(f"--now: brought {result.rowcount} web push delivery/deliveries forward")


async def report_deliveries(notification_id: str | None) -> None:
    from src.shared.database.session import get_session_factory

    where = (
        'WHERE d."notificationId" = :n'
        if notification_id
        else "WHERE d.\"createdAt\" > now() - interval '10 minutes'"
    )
    factory = get_session_factory()
    async with factory() as session:
        rows = (
            (
                await session.execute(
                    text(
                        'SELECT d.id, d.status, d."attemptCount", d."failureCode", '
                        'd."suppressionReason", d."providerMessageId", d."nextAttemptAt" '
                        'FROM "NotificationDelivery" d '
                        f"{where} AND d.channel = 'WEB_PUSH' "
                        'ORDER BY d."createdAt" DESC LIMIT 10'
                    ),
                    {"n": notification_id} if notification_id else {},
                )
            )
            .mappings()
            .all()
        )
        attempts = (
            (
                await session.execute(
                    text(
                        'SELECT a."deliveryId", a."attemptNumber", a."errorCode", a."errorDetail", '
                        'a."responseMetadata" FROM "NotificationDeliveryAttempt" a '
                        'WHERE a."deliveryId" = ANY(:ids) ORDER BY a."attemptNumber"'
                    ),
                    {"ids": [r["id"] for r in rows] or [""]},
                )
            )
            .mappings()
            .all()
        )

    print("--- ledger ---")
    if not rows:
        print("  no WEB_PUSH delivery was planned. Nothing was sent, and the reason is above:")
        print("  a delivery is only planned when a live subscription exists at create time.")
    for row in rows:
        print(
            f"  {row['status']:10s} attempts={row['attemptCount']}"
            f" failure={row['failureCode'] or '—'}"
            f" suppressed={row['suppressionReason'] or '—'}"
        )
        if row["providerMessageId"]:
            print(f"    push service accepted it: {row['providerMessageId']}")
        if row["nextAttemptAt"]:
            print(f"    will retry at {row['nextAttemptAt']}")
    for attempt in attempts:
        detail = (attempt["errorDetail"] or "")[:160]
        print(
            f"    attempt {attempt['attemptNumber']}:"
            f" {attempt['responseMetadata'] or {}}"
            f"{'  ' + (attempt['errorCode'] or '') if attempt['errorCode'] else ''}"
            f"{'  ' + detail if detail else ''}"
        )


async def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--email")
    group.add_argument("--user-id")
    parser.add_argument(
        "--check", action="store_true", help="report the gates and stop, sending nothing"
    )
    parser.add_argument(
        "--dispatch-only",
        action="store_true",
        help="drain what is already queued without creating a notification",
    )
    parser.add_argument(
        "--now",
        action="store_true",
        help=(
            "bring this learner's planned web push forward to now, ignoring the daily attention "
            "budget. Moves only the schedule — consent is still rechecked at dispatch."
        ),
    )
    parser.add_argument(
        "--plan-only",
        action="store_true",
        help=(
            "create the notification and stop, leaving the deployed worker to send it. Use this "
            "when the subscription belongs to another environment: only that deployment can "
            "decrypt its subscription secrets, and dispatching from here would prune it."
        ),
    )
    args = parser.parse_args()

    # Registers the `User` mapper. `NotificationDelivery.userId` carries a foreign key to it, and
    # SQLAlchemy resolves that when it sorts tables for a flush — so claiming a delivery raises
    # `NoReferencedTableError` in any process that imported the notification models alone. The
    # Celery worker gets this for free through its own import chain; a standalone script does not.
    import src.domains.identity.db_models  # noqa: F401
    from src.domains.notifications.web_push_dispatcher import dispatch_due_web_push
    from src.shared.database.session import ensure_db

    await ensure_db()
    user_id, email = await _resolve_user(args.email, args.user_id)
    print(f"learner {email}  ({user_id})\n")

    ready = await report_gates(user_id)
    if args.check:
        print("\n--check: nothing sent.")
        return

    if args.plan_only:
        notification_id = await create_notification(user_id)
        if args.now:
            await bring_forward(user_id, notification_id)
        print(
            "\n--plan-only: planned but not sent. The deployed worker's "
            "`notifications.dispatch_web_push` beat task runs every 60s and will pick it up."
        )
        await report_deliveries(notification_id)
        return

    if not ready:
        print("\nOne of the gates above is closed, so nothing would be delivered.")
        print("Fix it and run again — sending anyway would only produce a misleading failure.")
        return

    notification_id = None if args.dispatch_only else await create_notification(user_id)
    if args.now:
        await bring_forward(user_id, notification_id)
    claimed = await dispatch_due_web_push()
    print(f"dispatcher claimed {claimed} delivery/deliveries\n")
    await report_deliveries(notification_id)
    print("\nIf the ledger says ACCEPTED, the push service took it. Anything after that is")
    print("the browser's: check that the tab is not muted and the OS allows notifications.")


if __name__ == "__main__":
    asyncio.run(main())
