"""One resolver. `resolve(user_id)` is the only thing that decides whether a learner is Plus.

Before this module there were four mechanisms answering that question and they disagreed
(MAIGIE_PLUS_COMMERCIAL_PLAN.md §2): `feature_tier_service` matched `User.tier.startswith("PREMIUM")`
and knew about trials; `require_premium` matched a six-tier tuple and did not; `credit_consumption_service`
keyed a seven-tier limits table; and the LLM model router read `User.tier` alone, so a learner on a
trial got Plus quiz modes and free-tier models in the same request. Adding passes to that would have
made a fifth opinion, and the pass is the one that changes minute to minute.

**Personal scope only.** `resolve()` takes a `user_id` and nothing else — no `space_id`, no optional
scope argument — so it cannot quietly become the Space resolver later. Space-scoped entitlement stays
in `feature_flags.effective_tier_for_request`'s `seat_tier` branch and in `seat_impl` (Decision F).

Precedence, highest first: **subscription → active pass → trial → free.** A subscriber outranks a pass
so that a pass is never silently burned by someone who already has Plus (Decision D).

Passes do not exist yet. `_read_active_pass` is the seam Phase 4 fills in, and it is a named function
rather than a `TODO` so that the precedence rule can be written, tested and reviewed once — see
`_compose`, which is pure and total and does not know where any of its arguments came from.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Literal

logger = logging.getLogger(__name__)

EntitlementTier = Literal["free", "plus"]
EntitlementSource = Literal["none", "subscription", "pass", "trial"]


# ===========================================================================
# Tier map
# ===========================================================================

# One frozenset, one member, and an explicit map rather than a prefix.
#
# `startswith("PREMIUM")` was the bug in drift item 10: `STUDY_CIRCLE_*` and `SQUAD_*` are
# personal `User.tier` values that did not match it, so `feature_tier_service` resolved them to
# `"free"` and denied every capability while `CREDIT_LIMITS` granted them 500k–12M credits.
#
# Revision 3 of the plan fixed that with a `LEGACY_PLUS_TIERS` frozenset holding `PREMIUM_YEARLY`,
# `STUDY_CIRCLE_MONTHLY` / `_YEARLY` and `SQUAD_MONTHLY` / `_YEARLY`, resolving all five to `plus` so
# that subscribers on withdrawn products were not denied what they were paying for. Revision 4
# removed it: there are no such subscribers, so the bug has no victim and the fix has no
# beneficiary. Those five strings resolve to `free`, which is now the correct answer rather than a
# defect, and a `User.tier` holding one of them is a data error rather than a supported state.
#
# If a live subscription on any of them is ever found, restore the frozenset — breaking someone who
# is paying us is not a trade worth the tidier code.
PLUS_TIERS = frozenset({"PREMIUM_MONTHLY"})


# ===========================================================================
# Window allowances (§6.3)
# ===========================================================================

# Charged units per 5-hour window. Phase 3 introduces the window itself and repoints
# `credit_consumption_service` at `Entitlement.window_allowance` in place of `CREDIT_LIMITS[tier]`;
# the numbers live here because the allowance is a property of the entitlement, not of the meter.
WINDOW_ALLOWANCE_FREE = 500
WINDOW_ALLOWANCE_PLUS = 4_000
WINDOW_ALLOWANCE_PASS_5H = 3_000
WINDOW_ALLOWANCE_PASS_7D = 4_000

# Keyed by the catalogue product id a pass was bought as, so Phase 4 adds a pass product by adding
# a row here rather than by editing `_compose`.
WINDOW_ALLOWANCE_BY_PASS_PRODUCT: dict[str, int] = {
    "plus_pass_5h": WINDOW_ALLOWANCE_PASS_5H,
    "plus_pass_7d": WINDOW_ALLOWANCE_PASS_7D,
}


# ===========================================================================
# Shapes
# ===========================================================================


@dataclass(frozen=True)
class Entitlement:
    """What a learner is entitled to in their personal workspace, right now."""

    tier: EntitlementTier
    source: EntitlementSource
    expires_at: datetime | None
    """Pass expiry, subscription period end, or trial end. `None` for free."""
    pass_id: str | None
    subscription_tier: str | None
    """The raw `User.tier`, for display and history. Populated whatever the resolved tier."""
    is_trial: bool
    trial_days_remaining: int | None
    window_allowance: int


@dataclass(frozen=True)
class ActivePass:
    """A pass that is currently running. Phase 4 is where these start existing."""

    pass_id: str
    product_id: str
    expires_at: datetime


@dataclass(frozen=True)
class ActiveTrial:
    ends_at: datetime
    days_remaining: int


# ===========================================================================
# The precedence rule
# ===========================================================================


def _compose(
    *,
    subscription_tier: str | None,
    subscription_period_end: datetime | None,
    active_pass: ActivePass | None,
    active_trial: ActiveTrial | None,
) -> Entitlement:
    """Apply subscription → pass → trial → free to already-read state.

    Pure and total, and separated from the reads for two reasons. It is the only part of this
    module that encodes a product decision, so it is the part worth testing exhaustively; and it
    lets the pass branch be written and asserted before `PlusPass` exists, so Phase 4 wires a
    reader rather than reopening the precedence question.
    """
    raw_tier = subscription_tier or "FREE"

    if raw_tier in PLUS_TIERS:
        return Entitlement(
            tier="plus",
            source="subscription",
            expires_at=subscription_period_end,
            pass_id=None,
            subscription_tier=raw_tier,
            is_trial=False,
            trial_days_remaining=None,
            window_allowance=WINDOW_ALLOWANCE_PLUS,
        )

    if active_pass is not None:
        return Entitlement(
            tier="plus",
            source="pass",
            expires_at=active_pass.expires_at,
            pass_id=active_pass.pass_id,
            subscription_tier=raw_tier,
            is_trial=False,
            trial_days_remaining=None,
            window_allowance=WINDOW_ALLOWANCE_BY_PASS_PRODUCT.get(
                active_pass.product_id, WINDOW_ALLOWANCE_PASS_5H
            ),
        )

    if active_trial is not None:
        return Entitlement(
            tier="plus",
            source="trial",
            expires_at=active_trial.ends_at,
            pass_id=None,
            subscription_tier=raw_tier,
            is_trial=True,
            trial_days_remaining=active_trial.days_remaining,
            # A trialling learner is indistinguishable from a subscriber, including here. The old
            # model router gave them Plus capabilities and free-tier models; that was drift 11.
            window_allowance=WINDOW_ALLOWANCE_PLUS,
        )

    return Entitlement(
        tier="free",
        source="none",
        expires_at=None,
        pass_id=None,
        subscription_tier=raw_tier,
        is_trial=False,
        trial_days_remaining=None,
        window_allowance=WINDOW_ALLOWANCE_FREE,
    )


FREE_ENTITLEMENT = _compose(
    subscription_tier="FREE",
    subscription_period_end=None,
    active_pass=None,
    active_trial=None,
)
"""What an unreadable or unknown user resolves to. A failure to read an entitlement gates as Free
rather than failing the request, matching `feature_flags._fetch_personal_tier`."""


# ===========================================================================
# Reads
# ===========================================================================


async def resolve(user_id: str) -> Entitlement:
    """Resolve a learner's personal entitlement. Called on nearly every gated request."""
    # Imported here, not at module scope: `personal_learning.services.feature_tier_service` imports
    # this module, so a top-level import of anything under `personal_learning` closes a cycle.
    from sqlalchemy import select

    from src.domains.identity.db_models import User
    from src.domains.personal_learning.db_models import LearningProfile
    from src.shared.database.session import get_session_factory

    # One round trip. Decision C's whole argument is that `resolve()` sits in the hot path, and the
    # trial lives on a different table from the tier, so the join is what keeps this to a single
    # read instead of two — reaching for `PersonalLearningRepository` here would cost a second.
    stmt = (
        select(
            User.tier,
            User.subscription_current_period_end,
            LearningProfile.trial_ends_at,
        )
        .outerjoin(LearningProfile, LearningProfile.user_id == User.id)
        .where(User.id == user_id)
    )

    try:
        factory = get_session_factory()
        async with factory() as session:
            row = (await session.execute(stmt)).first()
    except Exception:
        logger.exception("entitlement resolve failed for user %s; gating as free", user_id)
        return FREE_ENTITLEMENT

    if row is None:
        return FREE_ENTITLEMENT

    raw_tier, period_end, trial_ends_at = row

    return _compose(
        subscription_tier=str(raw_tier) if raw_tier else "FREE",
        subscription_period_end=period_end,
        active_pass=await _read_active_pass(user_id),
        active_trial=_active_trial(trial_ends_at),
    )


def _active_trial(trial_ends_at: datetime | None) -> ActiveTrial | None:
    """Build the trial half of the input, or `None` if there is no trial running."""
    if trial_ends_at is None:
        return None
    now = datetime.now(UTC)
    if now >= trial_ends_at:
        return None
    # Floored, matching `trial_service.get_trial_status:179`. Two places computing days remaining
    # differently is how a learner sees "2 days left" on one screen and "3" on another.
    return ActiveTrial(
        ends_at=trial_ends_at,
        days_remaining=max(0, (trial_ends_at - now).days),
    )


async def _read_active_pass(user_id: str) -> ActivePass | None:
    """Read the learner's running pass.

    **Always `None` until Phase 4.** `PlusPass` and the `activePlusPassId` /
    `activePlusPassExpiresAt` columns on `User` are created by that phase's migration, so there is
    nothing to read yet and pretending otherwise would mean querying a table that does not exist.

    This exists as a named seam rather than as a comment inside `resolve` so that the pass branch of
    `_compose` is reachable, tested and reviewed now — the expensive part of passes is the
    precedence question, not the query. Phase 4 replaces this body and changes nothing else here.
    """
    return None
