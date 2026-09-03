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

Passes exist as of Phase 4. `_active_pass` builds the pass half from columns the same joined read
already fetched (Decision C), and applies **both** of Decision E's endings on read — a pass whose wall
clock has passed and one whose allowance is spent are equally over, and both are over the instant they
are read rather than when the five-minute sweep next runs. `_compose` is unchanged by any of it, which
was the point of writing it pure and total before there was a table.
"""

from __future__ import annotations

import logging
from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
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
# removed it on the product fact that there are no such subscribers.
#
# Phase 2a restored it, because dropping it changed nothing about what *writes* `User.tier` — a
# yearly renewal would have been charged, verified, written, and then resolved to `free`. Phase 2b
# removed it again, this time on a measurement rather than a recollection.
#
# `scripts/count_legacy_commercial_state.py`, run against production 2026-09-01:
#
#     users on a retired tier (PREMIUM_YEARLY / STUDY_CIRCLE_* / SQUAD_*)   0
#     users with a Stripe subscription id                                    0
#     users with a Paystack subscription code                                0
#     users with a Google Play purchase token                                0
#     users with a non-zero purchased credit balance                         0
#     CreditPurchaseTransaction rows with status = 'completed'               0
#     tiers: FREE 1205, PREMIUM_MONTHLY 1
#
# So there is nobody to grandfather, and no payment relationship exists anywhere in the database.
# The single `PREMIUM_MONTHLY` row has no Stripe, Paystack or Play identifier against it, so it is a
# tier set by hand rather than a subscription — it keeps Plus either way, since `PREMIUM_MONTHLY` is
# the tier still on sale, and its null `subscriptionCurrentPeriodEnd` is why `_subscription_lapsed`
# treats absent as "not lapsed" rather than as expired.
#
# The five retired strings now resolve to `free`, which is the correct answer rather than a defect:
# a `User.tier` holding one is a data error. Phase 2b removes the writers that could produce them in
# the same change, so the resolver and the writers stay in agreement — which is the property whose
# absence made the first removal wrong.
#
# **If a live subscription on any of them is ever found, restore the frozenset.** Re-run the script
# rather than re-deriving the argument.
PLUS_TIERS = frozenset({"PREMIUM_MONTHLY"})


# ===========================================================================
# Window allowances (§6.3)
# ===========================================================================

# Charged units per 5-hour window. Phase 3 introduces the window itself and repoints
# `credit_consumption_service` at `Entitlement.window_allowance` in place of `CREDIT_LIMITS[tier]`;
# the numbers live here because the allowance is a property of the entitlement, not of the meter.
# `WINDOW_ALLOWANCE_PASS_5H` is 2_000, down from 3_000, and it moved because the *monthly price*
# moved. At $0.99 for 3 000 units a pass cost $0.00025/unit while the $9.99 subscription cost
# $0.00045/unit — the value product was the worst deal per unit, and a learner doing arithmetic
# would buy passes forever. The ladder now runs 5h $0.000375 > 7d $0.000348 > monthly $0.000249,
# which is the intended order: impulse buys cost most per unit. See §6.4.
WINDOW_ALLOWANCE_FREE = 500
WINDOW_ALLOWANCE_PLUS = 4_000
WINDOW_ALLOWANCE_PASS_5H = 2_000
WINDOW_ALLOWANCE_PASS_7D = 4_000

# Keyed by the catalogue product id a pass was bought as, so Phase 4 adds a pass product by adding
# a row here rather than by editing `_compose`.
#
# `plus_pass_term` was missing from this table while being present in `VOICE_SECONDS_BY_PASS_PRODUCT`,
# and the failure was silent in the worst way: `_compose` falls back to the *smallest* window allowance
# for an unknown product, so a four-month Term Pass would have run on the 5-hour pass's 2 000 units per
# window. The fallback is right — under-granting is a support ticket and over-granting is COGS — which
# is exactly why a missing row reads as working software. `test_plus_passes` now asserts all three
# tables carry the same ids.
WINDOW_ALLOWANCE_BY_PASS_PRODUCT: dict[str, int] = {
    "plus_pass_5h": WINDOW_ALLOWANCE_PASS_5H,
    "plus_pass_7d": WINDOW_ALLOWANCE_PASS_7D,
    # §6.3: 4 000 units/window, the same per-window figure as the subscription. The Term Pass is a
    # monthly-shaped product sold four months at a time, so it is bounded by its 20 000-unit total
    # rather than by a smaller window.
    "plus_pass_term": WINDOW_ALLOWANCE_PLUS,
}

# The monthly backstop (§6.3). **Not a product limit — an abuse limit.**
#
# A 5-hour tumbling window permits up to 4.8 windows a day, so monthly exposure is 144× the window
# allowance and no window figure is simultaneously generous enough for one session and bounded enough
# for a month. The backstop is set at ~9 Plus windows a month, far above what studying reaches: two
# windows a day for twenty days is forty windows, but a typical window consumes well under its
# allowance, so this binds only on sustained maximal draw.
#
# `MONTHLY_BACKSTOP_PLUS` is 36_000, raised from 30_000 when the price went to $9.99. **A price rise
# that arrives with a larger allowance is defensible to a learner; a bare one is not** — and the
# arithmetic requires it too, or the subscription costs more per unit than a pass (see the note above
# `WINDOW_ALLOWANCE_PASS_5H`). 36 000 units is roughly 1 200 Flash-Lite chat turns a month, about 40
# a day, which is generous rather than nominally so. Floor margin at the ceiling is 60% on units, or
# 46% once the 60 included voice minutes are counted with them.
#
# It is not shown in the UI, not in the marketing, and not in `GET /billing/usage` until a learner is
# within 20% of it. Experientially there is no monthly limit; financially there is a bound.
#
# A pass carries no monthly backstop of its own — a 5-hour pass is bounded by its own single window
# and a 7-day pass by `PlusPass.unitsAllowance` (Decision E), both of which Phase 4 introduces. Until
# then the pass branch reports `None` and the meter reads it as unbounded, which is correct: nothing
# can hold a pass yet.
MONTHLY_BACKSTOP_FREE = 5_000
MONTHLY_BACKSTOP_PLUS = 36_000


# ===========================================================================
# Voice allowances (§6.3)
# ===========================================================================

# Live voice has its own counter and is **not** drawn from the window above. At 200 units/minute a
# voice minute is 40× a Flash-Lite chat turn, so one allowance covering both dominated every ceiling:
# it had to be priced for the voice case and was spent almost entirely on the text case, which is the
# worst of both — a price defensible only against a cost almost nobody incurred.
#
# Stated in seconds because the billing loop measures seconds. A 2-second tick against a
# minute-denominated counter either rounds every tick up to a minute or rounds it down to nothing.
#
# **Free gets zero, and that is the honest version of the paywall rather than a saving.** An earlier
# draft gave Free 2.5 minutes *per window* — but a 5-hour window permits 4.8 windows a day, so that is
# 12 minutes daily, $0.24/day, **$7.20/month at zero revenue**, from a tier whose entire target COGS
# is $0.20. It was not a small grant of voice; it was an unbounded one wearing a per-window label.
# Zero is defensible in a way that 2.5 is not, and it makes voice the one capability a free learner is
# told plainly they do not have — which is also the clearest thing a pass can be sold on.
VOICE_SECONDS_FREE = 0
VOICE_SECONDS_PLUS_MONTHLY = 60 * 60  # 60 minutes per subscription period
VOICE_SECONDS_PASS_5H = 10 * 60
VOICE_SECONDS_PASS_7D = 25 * 60

# Same shape as `WINDOW_ALLOWANCE_BY_PASS_PRODUCT`: Phase 4 adds a pass by adding a row, not by
# editing `_compose`. The Term Pass grants the monthly figure because it is a monthly product sold
# four months at a time (§6.8).
VOICE_SECONDS_BY_PASS_PRODUCT: dict[str, int] = {
    "plus_pass_5h": VOICE_SECONDS_PASS_5H,
    "plus_pass_7d": VOICE_SECONDS_PASS_7D,
    "plus_pass_term": VOICE_SECONDS_PLUS_MONTHLY,
}

#: What `plus_voice_30` adds. Purchased seconds are held separately and never expire, so this is a
#: top-up rather than a new allowance — see `voice_service`.
VOICE_SECONDS_TOP_UP = 30 * 60


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
    monthly_backstop: int | None
    """Units per calendar month, or `None` for an entitlement bounded some other way.

    An abuse limit rather than a product limit (§6.3), which is why it is separate from
    `window_allowance` rather than derived from it: the ratio between them is a judgement about
    sustained draw, not arithmetic.
    """
    voice_seconds_included: int
    """Live-voice seconds this entitlement grants, drawn from its own counter and not the window.

    Zero means voice is **not available**, not "available and empty", and the two need different copy:
    a free learner has to be told voice is a Plus capability rather than that they have used up an
    allowance of nothing. `voice_available` is that distinction, kept as a property so no caller has to
    re-derive it from a zero.
    """
    voice_allowance_source_id: str | None
    """What a granted voice balance belongs to, so expiry needs no sweep.

    `"subscription:{period_end}"`, `"pass:{pass_id}"`, or `None` for a tier with no voice. When this
    stops matching the value stored on `User`, the balance is stale and `voice_service` re-grants —
    which is how a renewal tops up and how a pass takes its minutes with it when it ends.
    """

    @property
    def voice_available(self) -> bool:
        """Whether live voice is a capability this learner has at all."""
        return self.voice_seconds_included > 0


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

    if raw_tier in PLUS_TIERS and not _subscription_lapsed(subscription_period_end):
        return Entitlement(
            tier="plus",
            source="subscription",
            expires_at=subscription_period_end,
            pass_id=None,
            subscription_tier=raw_tier,
            is_trial=False,
            trial_days_remaining=None,
            window_allowance=WINDOW_ALLOWANCE_PLUS,
            monthly_backstop=MONTHLY_BACKSTOP_PLUS,
            voice_seconds_included=VOICE_SECONDS_PLUS_MONTHLY,
            # Keyed on the period end, which is what makes a renewal re-grant with no sweep and no
            # job: the id changes when the period does, so the next read after a renewal finds a
            # source it does not recognise and tops the balance back up. A period end of `None` — a
            # hand-set tier with no subscription behind it, which is what the one `PREMIUM_MONTHLY`
            # row in production is — grants once and never again, which is the right answer for a
            # tier nobody is billing for.
            voice_allowance_source_id=f"subscription:{subscription_period_end}",
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
            # A pass is bounded by its own allowance, not by the calendar. Decision E.
            monthly_backstop=None,
            voice_seconds_included=VOICE_SECONDS_BY_PASS_PRODUCT.get(
                # Same defensive floor as the window allowance: an unknown pass product falls to the
                # *smallest* grant, because under-granting is a support ticket and over-granting is
                # COGS on the most expensive operation in the product.
                active_pass.product_id,
                VOICE_SECONDS_PASS_5H,
            ),
            # Keyed on the pass rather than on its expiry, so activating a second pass after the
            # first ends is a new source and a new grant. This is what replaces the plan's sweep:
            # when the pass stops being the active entitlement the id stops matching, and the next
            # read discards the balance rather than waiting for a job to notice.
            voice_allowance_source_id=f"pass:{active_pass.pass_id}",
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
            monthly_backstop=MONTHLY_BACKSTOP_PLUS,
            # A trial includes voice, and it is the one grant here worth pausing on: 60 minutes at
            # $0.02/minute is $1.20 of inference given to someone who has paid nothing, against a
            # 3-day trial. It stays because a trial that withholds the one capability Free is missing
            # is not a trial of Plus — and Decision B's whole point is that a trialling learner is
            # indistinguishable from a subscriber. The bound is the 3 days, not a smaller allowance.
            voice_seconds_included=VOICE_SECONDS_PLUS_MONTHLY,
            # Keyed on the trial end so a second trial after the cooldown is a fresh grant, and so a
            # learner cannot re-trial their way to unlimited voice inside one trial.
            voice_allowance_source_id=f"trial:{active_trial.ends_at}",
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
        monthly_backstop=MONTHLY_BACKSTOP_FREE,
        voice_seconds_included=VOICE_SECONDS_FREE,
        # Null rather than `"free:..."`: there is nothing to grant, so there is nothing to identify.
        # A free learner's stored source stays null forever, which is also what makes the re-grant
        # check cheap for the 1 205 accounts that are on Free.
        voice_allowance_source_id=None,
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
# Request-scoped memo
# ===========================================================================

# Collapsing four disagreeing mechanisms into one resolver replaced their reads with *its* read, and
# a single request now asks the same question several times: `feature_tier_service.check_capability`
# and `feature_flags.get_quality_tier` resolve independently, and the ask path pays this join per
# turn where it previously read a tier that had already been loaded with the user. Same answer, once
# per request, is the whole of the optimisation.
#
# **The cache only exists inside an explicitly opened scope**, and `EntitlementScopeMiddleware` opens
# one per HTTP request and no others. That is a correctness property rather than a simplification.
# A task-scoped or process-scoped memo would be held for the life of the task, and the longest-lived
# task in this codebase is a `study_voice` relay: a session runs for minutes, bills every tick, and
# must notice a pass expiring underneath it. Websocket scopes therefore get no cache and keep
# resolving fresh, which is exactly the behaviour a metered long-lived connection needs.
#
# The window is one request, so the only staleness reachable is a write earlier in the *same*
# request. `invalidate()` exists for that; `trial_service` is its caller, because starting a trial is
# the one thing that changes a learner's own entitlement inside a request they made themselves.
# Provider webhooks and store callbacks write tiers in requests of their own, which each have their
# own scope and read nothing entitlement-shaped afterwards.
_REQUEST_CACHE: ContextVar[dict[str, Entitlement] | None] = ContextVar(
    "entitlement_request_cache", default=None
)


@contextmanager
def request_scope() -> Iterator[None]:
    """Open a memo scope for the duration of one request.

    Nesting is safe: the inner scope shadows the outer one and `reset` restores it, so a scope
    opened in a test around a scope opened by middleware does not leak either way.
    """
    token = _REQUEST_CACHE.set({})
    try:
        yield
    finally:
        _REQUEST_CACHE.reset(token)


def invalidate(user_id: str) -> None:
    """Forget a memoised entitlement after writing something that changes it.

    A no-op outside a scope, which is why callers do not have to know whether they are in one.
    """
    cache = _REQUEST_CACHE.get()
    if cache is not None:
        cache.pop(user_id, None)


# ===========================================================================
# Reads
# ===========================================================================


async def resolve(user_id: str) -> Entitlement:
    """Resolve a learner's personal entitlement. Called on nearly every gated request.

    Memoised for the rest of the request when a scope is open; see `_REQUEST_CACHE` for why that is
    "when a scope is open" rather than "always".
    """
    cache = _REQUEST_CACHE.get()
    if cache is not None:
        cached = cache.get(user_id)
        if cached is not None:
            return cached

    entitlement = await _resolve_uncached(user_id)

    if cache is not None:
        cache[user_id] = entitlement
    return entitlement


async def _resolve_uncached(user_id: str) -> Entitlement:
    """The read itself. Separate from `resolve` so the memo has nothing to do with the query."""
    # Imported here, not at module scope: `personal_learning.services.feature_tier_service` imports
    # this module, so a top-level import of anything under `personal_learning` closes a cycle.
    from sqlalchemy import select

    from src.domains.billing.db_models import PlusPass
    from src.domains.identity.db_models import User
    from src.domains.personal_learning.db_models import LearningProfile
    from src.shared.database.session import get_session_factory

    # One round trip. Decision C's whole argument is that `resolve()` sits in the hot path, and the
    # tier, the trial and the pass live on three tables, so the joins are what keep this to a single
    # read instead of three — reaching for a repository here would cost the other two.
    stmt = (
        select(
            User.tier,
            User.subscription_current_period_end,
            LearningProfile.trial_ends_at,
            # The pass, joined rather than fetched separately. Decision C denormalises
            # `activePlusPassId` onto `User` precisely so this join is a primary-key lookup that
            # matches nothing for the overwhelming majority of learners — one round trip either way,
            # and no second query for the 1 205 accounts holding no pass.
            #
            # The *snapshotted* columns are read rather than the product table: `unitsAllowance` is
            # what this pass was sold with, which is not necessarily what the product carries today
            # (§6.8's NGN allowances differ, and re-pricing must not touch a pass already sold).
            PlusPass.id,
            PlusPass.product_id,
            PlusPass.expires_at,
            PlusPass.units_allowance,
            PlusPass.units_used,
        )
        .outerjoin(LearningProfile, LearningProfile.user_id == User.id)
        .outerjoin(PlusPass, PlusPass.id == User.active_plus_pass_id)
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

    (
        raw_tier,
        period_end,
        trial_ends_at,
        pass_id,
        pass_product_id,
        pass_expires_at,
        pass_units_allowance,
        pass_units_used,
    ) = row

    return _compose(
        subscription_tier=str(raw_tier) if raw_tier else "FREE",
        subscription_period_end=period_end,
        active_pass=_active_pass(
            pass_id=pass_id,
            product_id=pass_product_id,
            expires_at=pass_expires_at,
            units_allowance=pass_units_allowance,
            units_used=pass_units_used,
        ),
        active_trial=_active_trial(trial_ends_at),
    )


def _active_pass(
    *,
    pass_id: str | None,
    product_id: str | None,
    expires_at: datetime | None,
    units_allowance: int | None,
    units_used: int | None,
) -> ActivePass | None:
    """Build the pass half of the input, or `None` if no pass is currently running.

    **Replaces the `_read_active_pass` seam Phase 2 left returning `None`.** That seam existed so the
    pass branch of `_compose` could be written, tested and reviewed before `PlusPass` existed — the
    expensive part of passes being the precedence question rather than the query. This is the query,
    and `_compose` is unchanged.

    **Decision E's two endings are both applied here, on read.** A pass whose wall clock has passed and
    a pass whose allowance is spent are equally over, and both are over *the instant they are read*
    rather than when the five-minute sweep next runs. The sweep exists to write the status and tell the
    learner, not to make the expiry true — a learner must never be granted Plus by a pass that ended
    four minutes ago because a job has not caught up.
    """
    if pass_id is None or expires_at is None:
        return None

    if expires_at.tzinfo is None:
        # Defensive, matching `_subscription_lapsed`: the column is `DateTime(timezone=True)`, but a
        # naive value from a fixture would raise on comparison rather than answer the question.
        expires_at = expires_at.replace(tzinfo=UTC)
    if datetime.now(UTC) >= expires_at:
        return None

    # Decision E. The allowance is a real ending, not a soft cap: "capabilities without limit, usage
    # with a stated ceiling", and the ceiling was on the purchase screen. Without this the pass is the
    # product that loses money faster the more it is used — five hours of continuous live voice is
    # about $6.00 of inference against $0.75 of net revenue.
    if units_allowance is not None and (units_used or 0) >= units_allowance:
        return None

    return ActivePass(
        pass_id=pass_id,
        product_id=str(product_id or ""),
        expires_at=expires_at,
    )


def _subscription_lapsed(period_end: datetime | None) -> bool:
    """Whether a stored paid tier has outlived the period that was paid for.

    Pass and trial both expire lazily on read, and until Phase 2a a subscription did not: `_compose`
    returned `plus` for a `PREMIUM_MONTHLY` row whatever `subscription_current_period_end` said,
    even though it already had the value in hand. Two of three sources failed closed on a stale
    timestamp and one failed open.

    That is only safe if webhooks are the sole writer of `User.tier` *and* they always land. Neither
    holds today — `handle_paystack_webhook` reaches a Prisma sentinel until Phase 2b, so a
    `subscription.disable` can be lost, and a lost cancellation means a tier that never returns to
    `FREE`. This bounds that exposure to one billing period without depending on webhook health.

    `None` is treated as **not** lapsed. A missing period end means we never recorded one rather
    than that it has passed, and inferring cancellation from absent data would revoke access from
    subscribers whose row predates the field being written. `billing.check_expired_trials` is the
    job that reconciles genuinely stale rows.
    """
    if period_end is None:
        return False
    if period_end.tzinfo is None:
        # Defensive: the column is `DateTime(timezone=True)`, but a naive value read back from a
        # provider payload or a fixture would raise on comparison rather than answer the question.
        period_end = period_end.replace(tzinfo=UTC)
    return datetime.now(UTC) >= period_end


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


#: `_read_active_pass` is gone, and its replacement is `_active_pass` above.
#:
#: It was a named seam returning `None` while `PlusPass` did not exist, so that `_compose`'s pass branch
#: could be written and tested before there was anything to read. Phase 4 filled it — and moved it,
#: because the seam took a `user_id` and would have cost a second query. The pass now arrives on the same
#: joined read as the tier and the trial (Decision C), so `_active_pass` is a pure function of columns
#: rather than a reader, which is also what makes Decision E's two endings testable without a database.
