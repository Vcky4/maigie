"""Closing the bill after a session ends.

During a session the billing loop charges in batches, so when the socket closes there is almost always a
remainder: the seconds since the last flush, plus — for a FREE learner — the difference between what the time
was worth and the session minimum. This collects both.

It runs detached from the socket on purpose. The learner has already hung up, and making them wait on two
database writes and a usage row would add latency to leaving.

The one thing this must not do is skip on failure. A session that crashed still burned provider minutes, so
the snapshot is taken on every exit path in `bridge.run_bridge`, including the error and cancellation ones.
"""

from __future__ import annotations

import logging

from src.config import get_settings
from src.domains.billing.services import voice_service
from src.domains.billing.services.usage_tracking import (
    PERSONAL_USAGE_SCOPE,
    emit_ai_usage,
)

from . import session_store
from .billing import chargeable_seconds_final_settlement, chargeable_seconds_raw
from .bridge import BillingSnapshot

logger = logging.getLogger(__name__)


async def settle(user_id: str, session_id: str, snapshot: BillingSnapshot) -> None:
    """Charge whatever the session still owes and record the usage.

    Nothing is charged when billing never started — a session that failed before the provider completed
    setup produced no audio, and the learner should not pay for our failure to connect.
    """
    if not snapshot.billing_started:
        logger.info(
            "Voice session %s ended before billing started — nothing to settle",
            session_id,
        )
        return

    # The floor is charged once per session, not once per socket. A client that reconnects after a dropped
    # connection re-enters the same session id, and each attempt settles separately — so without this a
    # FREE learner on a flaky connection pays the minimum several times for one sitting.
    if await session_store.claim_session_floor(session_id):
        total = chargeable_seconds_final_settlement(
            snapshot.billable_seconds, snapshot.billing_mode
        )
    else:
        total = chargeable_seconds_raw(snapshot.billable_seconds)
    outstanding = max(0, total - snapshot.charged_seconds)

    try:
        if outstanding:
            # `voice_service.spend` never raises and never refuses, so the `SubscriptionLimitError`
            # branch this used to carry is gone. A learner who ran out partway through has already had
            # the session ended by `bridge.charge`; what is left here is recording the remainder, and
            # the balance simply floors at zero. The shortfall is logged inside `spend` rather than
            # surfaced as an exception, because a settlement that "fails" for want of balance is not a
            # failure — it is the meter telling us we served more than we sold.
            balance = await voice_service.spend(user_id, outstanding)
            remaining = balance.total_seconds
        else:
            remaining = (await voice_service.read_balance(user_id)).total_seconds
        logger.info(
            "Settled voice session %s: %.1fs billable (%s), %ds chargeable, %ds now, %ds left",
            session_id,
            snapshot.billable_seconds,
            snapshot.billing_mode,
            total,
            outstanding,
            remaining,
        )
    except Exception:
        logger.exception("Failed to settle voice session %s for user %s", session_id, user_id)

    # Usage is recorded separately from credits, and with zero tokens, because voice is billed by time. The
    # row exists so a voice session appears in usage analytics at all; without it, the most expensive
    # operation in the product is the only one that leaves no trace.
    await emit_ai_usage(
        user_id=user_id,
        usage_scope=PERSONAL_USAGE_SCOPE,
        provider="gemini",
        model=(get_settings().GEMINI_LIVE_MODEL or "").strip() or None,
        feature="gemini_live_voice",
        input_tokens=0,
        output_tokens=0,
        request_count=1,
    )
