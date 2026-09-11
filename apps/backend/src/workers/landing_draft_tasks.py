"""Landing draft background tasks.

One job: the retention sweep that marks, redacts and deletes expired anonymous drafts. Routed to the
'default' queue — it is three bounded statements per batch and touches no LLM.

Copyright (C) 2025 Maigie

Licensed under the Business Source License 1.1 (BUSL-1.1).
See LICENSE file in the repository root for details.
"""

import logging

from celery.schedules import crontab

from src.core.celery_app import celery_app

logger = logging.getLogger(__name__)


@celery_app.task(
    name="landing_drafts.sweep_expired",
    queue="default",
    time_limit=300,
    soft_time_limit=280,
)
def sweep_expired_drafts_task() -> dict:
    """Delete expired landing drafts and clear the email addresses on them.

    Unlike most sweeps in this codebase this one is load-bearing for a published promise rather than
    housekeeping: the privacy policy tells visitors an unclaimed draft is deleted, and this is the
    only thing that deletes it.
    """
    import asyncio

    from src.domains.landing_drafts.retention import sweep_expired
    from src.shared.database.session import ensure_db

    async def _run() -> dict:
        await ensure_db()
        return await sweep_expired()

    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(_run())
    finally:
        loop.close()


def get_beat_schedule() -> dict:
    return {
        "landing_drafts.sweep_expired": {
            "task": "landing_drafts.sweep_expired",
            # Daily, in the small hours, offset from the notification sweep at 03:30 so two delete
            # jobs are not competing for the same window. Daily is the right resolution because both
            # windows are measured in days, and every step is idempotent — a run with nothing to do
            # returns zeros, which is the steady state.
            "schedule": crontab(hour=4, minute=10),
            "options": {"queue": "default"},
        },
    }
