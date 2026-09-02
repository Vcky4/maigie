"""The usage meter: a rolling 5-hour window denominated in measured cost.

What this replaced, and why (MAIGIE_PLUS_COMMERCIAL_PLAN.md §6.2, §6.3):

**The unit was a token, and that is why voice was mispriced by two orders of magnitude.** A credit
was one token, `TOKEN_MULTIPLIER = 0.2` scaled it to flatter the number, and `CREDIT_COSTS` priced
each operation in tokens by hand. A voice minute was priced at 100 — as though it were 100 tokens of
text — when it costs what roughly 11 400 text tokens cost. Nothing was broken and no test looked at
the figure. A `usage_unit` is **$0.0001 of measured COGS**, so an operation cannot be mispriced by a
table being stale; it is priced by what it actually cost.

**The period was wrong.** A monthly hard cap, plus a daily cap for FREE only, plus an 80%-of-month
soft warning, plus a purchased-balance fallback, plus a referral daily-limit increase: five
interacting quantities. The failure a learner hit was "I ran out on the 9th and have three weeks of
nothing", and the message was a wall of formatted numbers. One window, one reset time, and running out
is never worse than five hours.

**What went away entirely:** `CREDIT_LIMITS`, `TOKEN_MULTIPLIER`, `apply_token_multiplier`,
`CREDIT_COSTS` as a fixed table, `get_credit_limits`, `initialize_user_credits`,
`reset_daily_credits_if_needed`, `ensure_credit_period`, `reset_credits_for_period_start`, the
purchased-balance fallback and the referral daily-limit increase. There are no paid users, so none of
it needed a migration path — it needed deleting.

**What is deliberately untouched: the `space_id` path.** Space usage draws on the Space's own credit
pool, is not personal-scope, and is out of scope for the whole plan (Decision F). Both entry points
below take the space branch before any window machinery is reached, which is what made this
separable.
"""

import logging
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import select

from src.domains.identity.db_models import LimitReachedEmailLog, User
from src.domains.identity.repository import IdentityRepository
from src.domains.learning_spaces.repository import space_repo
from src.shared.database import get_session_factory
from src.shared.exceptions import SubscriptionLimitError
from src.shared.infrastructure.email import send_limit_reached_email

logger = logging.getLogger(__name__)


# ===========================================================================
# The unit
# ===========================================================================

#: One usage unit is $0.0001 of measured cost — a hundredth of a cent, so 10 000 units is $1.00.
#:
#: Chosen so that the cheapest real operation is still more than one unit (a Flash-Lite chat turn is
#: ~29) and the most expensive fits in four digits (course generation is ~1 020). A coarser unit would
#: round the cheap operations to zero and let them run free; a finer one buys precision the rate card
#: does not have.
USD_PER_UNIT = 0.0001


def units_for_usd(cost_usd: float) -> int:
    """Convert measured cost to units, rounding up.

    Up, not nearest: rounding down would let an operation cheaper than half a unit cost nothing at
    all, and "free if small enough" is how an unmetered surface starts.
    """
    if cost_usd <= 0:
        return 0
    units = cost_usd / USD_PER_UNIT
    return max(1, int(units) + (1 if units % 1 else 0))


#: Where a refused learner is sent. Was `maigie://credits/purchase`, which pointed at a credit-pack
#: purchase flow for a product withdrawn in Phase 1 — a refusal that offered a dead link as its remedy.
#: The remedy for an exhausted window is a pass or a subscription, so it goes to the upgrade surface,
#: which is also where Decision N puts the conversion moment.
UPGRADE_DEEP_LINK = "maigie://plus/upgrade"


def units_for_tokens(input_tokens: int, output_tokens: int, model_name: str | None) -> int:
    """Price a completed generation from its real token counts and the model that produced it.

    The same operation costs different units on different tiers, because the tier picks the model.
    That is correct and self-balancing rather than a wrinkle to hide: a Plus learner gets a larger
    allowance *and* a dearer model, and the ratio between them is the margin.
    """
    from src.domains.billing.services.cost_calculator import calculate_ai_cost

    return units_for_usd(calculate_ai_cost(input_tokens, output_tokens, model_name=model_name))


#: Flat unit estimates for the operations that charge but cannot yet measure.
#:
#: These three reach a provider through `llm_resilient`, which discards the response object and
#: returns text only, so no caller sees `usage_metadata`. Phase 3b plumbs it through and **deletes
#: this table** — Decision L is explicit that cost is measured, not tabulated.
#:
#: Named as estimates rather than costs so nobody mistakes them for measurements. Each is the §6.5
#: figure for its operation, which is derived from `max_tokens` and the rate card rather than
#: observed. They are the last tabulated prices in the codebase.
ESTIMATED_OPERATION_UNITS = {
    # One model call producing one durable note the learner keeps.
    "voice_session_note": 110,
    # Merging several notes into one. Same price as writing one, because it is the same thing: one
    # call, one note. Charged once regardless of how many notes went in — the inputs were paid for
    # when they were written, and pricing per input would make consolidating a messy topic cost more
    # the messier it got.
    "note_merge": 110,
    # A Mermaid or maths diagram for Study Mode.
    "study_diagram": 100,
}


# ===========================================================================
# The window
# ===========================================================================

#: A tumbling window, started by the first billable operation and reset by the first one that occurs
#: after it has elapsed. Five hours for every tier, deliberately: one number to explain, and it is
#: also the duration of the 5-hour pass, so "a pass is one Plus session" is literally true.
WINDOW_HOURS = 5

#: The soft warning fires here. Carries the reset timestamp, which is the whole point — a warning
#: without a time is just an apology.
WINDOW_WARNING_FRACTION = 0.8

#: The monthly backstop is invisible until a learner is this close to it (§6.3).
BACKSTOP_DISCLOSURE_FRACTION = 0.8


@dataclass(frozen=True)
class WindowState:
    """A learner's window as of `now`, with any elapsed window already rolled over."""

    started_at: datetime
    units_used: int
    resets_at: datetime
    month_started_at: datetime
    month_units_used: int
    #: True when the read rolled a window or a month over, so the caller knows the stored row is
    #: stale and a write must persist the new boundaries rather than only the increment.
    rolled: bool


def _month_start(now: datetime) -> datetime:
    return now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)


def _as_utc(value: datetime | None) -> datetime | None:
    """Postgres returns tz-aware datetimes; SQLite in tests can return naive ones."""
    if value is None:
        return None
    return value if value.tzinfo else value.replace(tzinfo=UTC)


def window_state(user: User, now: datetime | None = None) -> WindowState:
    """Resolve the window from the stored columns, rolling over lazily.

    **Reads never write.** A learner who opens a page after six hours sees a full allowance because
    the window has elapsed, not because looking at it reset anything. The reset is attributed to the
    first billable operation after the window ends, which is what makes `resets_at` predictable: it is
    always `started_at + 5h` of a window that some operation actually opened.
    """
    now = now or datetime.now(UTC)
    started = _as_utc(user.usage_window_started_at)
    used = user.usage_window_units_used or 0
    month_started = _as_utc(user.usage_month_started_at)
    month_used = user.usage_month_units_used or 0

    rolled = False

    if started is None or now >= started + timedelta(hours=WINDOW_HOURS):
        started = now
        used = 0
        rolled = True

    current_month = _month_start(now)
    if month_started is None or month_started < current_month:
        month_started = current_month
        month_used = 0
        rolled = True

    return WindowState(
        started_at=started,
        units_used=used,
        resets_at=started + timedelta(hours=WINDOW_HOURS),
        month_started_at=month_started,
        month_units_used=month_used,
        rolled=rolled,
    )


@dataclass
class CreditConsumptionResult:
    """Result of a metered operation.

    No `source`, no `purchased_deducted`, no `purchased_balance_remaining`: there is one place usage
    is drawn from now. The old shape described a split between subscription and purchased balances
    that no longer exists, and every caller that read those fields was reading about a product that
    was withdrawn.
    """

    user: User
    units_consumed: int
    window_resets_at: datetime
    window_units_used: int
    window_allowance: int
    warning: str | None


# ===========================================================================
# Checks and consumption
# ===========================================================================


def _refusal(units_needed: int, resets_at: datetime, *, monthly: bool) -> SubscriptionLimitError:
    """The refusal a learner actually sees.

    One sentence with a time in it. What this replaced ran to five clauses across a daily cap, a
    monthly cap and a purchased balance, and told the learner three numbers they could do nothing
    with. `detail` carries the machine-readable reset so a client can render a countdown without
    parsing prose — `windowResetsAt` in the plan.
    """
    if monthly:
        return SubscriptionLimitError(
            message=(
                "You've reached your usage limit for this month. "
                "It resets at the start of next month."
            ),
            detail=f"units_required={units_needed}, limit=monthly_backstop",
            # Deliberately `None`. A monthly refusal is the one case where the window's reset time is
            # the wrong thing to show: it will pass in under five hours and change nothing.
            window_resets_at=None,
        )

    return SubscriptionLimitError(
        message="You've used this session's allowance. It refills automatically.",
        detail=f"units_required={units_needed}, windowResetsAt={resets_at.isoformat()}",
        # The time is carried structurally rather than written into the sentence. The message is
        # rendered in a client's own timezone and a server-side "3:40 PM UTC" is a small arithmetic
        # problem handed to a learner who has just been refused.
        window_resets_at=resets_at.isoformat(),
    )


def _warning(state: WindowState, allowance: int, units_after: int) -> str | None:
    if allowance <= 0:
        return None
    if units_after < allowance * WINDOW_WARNING_FRACTION:
        return None
    # No time in the sentence, for the same reason as the refusal: `CreditConsumptionResult` carries
    # `window_resets_at` alongside this, and the client renders it locally.
    return "You've used most of this session's allowance."


async def check_credit_availability(
    user: User,
    units_needed: int,
    db_client: Any | None = None,
    space_id: str | None = None,
) -> tuple[bool, str | None]:
    """Can this operation be paid for out of the current window?

    Args:
        user: User model instance.
        units_needed: Cost in usage units. **Units, not tokens** — callers used to pass raw token
            counts and this service scaled them by `TOKEN_MULTIPLIER`. Use `units_for_tokens` for a
            completed generation, or `ESTIMATED_OPERATION_UNITS` where measurement is not yet plumbed.
        db_client: Ignored; kept so the many existing call sites need no edit.
        space_id: When set, takes the Space branch and never touches the window (Decision F).

    Returns:
        `(is_available, warning_message)`. A warning is not a refusal: it fires at 80% of the window
        and carries the reset time so the learner can plan rather than be surprised.
    """
    if space_id:
        space = await space_repo.find_space_basic(space_id)
        if not space:
            raise ValueError(f"Space {space_id} not found")
        if space.credits_limit and space.credits + units_needed > space.credits_limit:
            return False, "Space credit limit reached."
        return True, None

    from src.domains.billing.services import entitlement_service

    identity_repo = IdentityRepository()
    fresh = await identity_repo.find_by_id(user.id)
    if not fresh:
        raise ValueError(f"User {user.id} not found")

    entitlement = await entitlement_service.resolve(fresh.id)
    state = window_state(fresh)

    if entitlement.monthly_backstop is not None:
        if state.month_units_used + units_needed > entitlement.monthly_backstop:
            return False, None

    if state.units_used + units_needed > entitlement.window_allowance:
        return False, None

    return True, _warning(state, entitlement.window_allowance, state.units_used + units_needed)


async def consume_credits(
    user: User,
    units: int,
    operation: str = "unknown",
    db_client: Any | None = None,
    space_id: str | None = None,
) -> CreditConsumptionResult:
    """Draw `units` from the learner's window, or refuse.

    Args:
        user: User model instance.
        units: Cost in usage units — see `check_credit_availability` on the change of denomination.
        operation: For logging and, once Phase 3's instrumentation lands, for per-operation measurement.
        db_client: Ignored; kept for call-site compatibility.
        space_id: When set, draws on the Space pool and never touches the window (Decision F).

    Raises:
        SubscriptionLimitError: When the window or the monthly backstop cannot fund the operation.
    """
    if space_id:
        return await _consume_space_credits(user, units, operation, space_id)

    from src.domains.billing.services import entitlement_service

    identity_repo = IdentityRepository()
    fresh = await identity_repo.find_by_id(user.id)
    if not fresh:
        raise ValueError("User not found after refresh")

    entitlement = await entitlement_service.resolve(fresh.id)
    state = window_state(fresh)

    if (
        entitlement.monthly_backstop is not None
        and state.month_units_used + units > entitlement.monthly_backstop
    ):
        await _notify_limit_reached(fresh, state)
        raise _refusal(units, state.resets_at, monthly=True)

    if state.units_used + units > entitlement.window_allowance:
        await _notify_limit_reached(fresh, state)
        raise _refusal(units, state.resets_at, monthly=False)

    updated = await identity_repo.update(
        fresh.id,
        {
            # Written whether or not the window rolled. The boundaries are only stale when `rolled`
            # is set, but writing them unconditionally costs nothing and removes a branch in which
            # the counter and its window could disagree.
            "usageWindowStartedAt": state.started_at,
            "usageWindowUnitsUsed": state.units_used + units,
            "usageMonthStartedAt": state.month_started_at,
            "usageMonthUnitsUsed": state.month_units_used + units,
        },
    )

    logger.info(
        "usage: user=%s operation=%s units=%d window=%d/%d month=%d/%s",
        fresh.id,
        operation,
        units,
        state.units_used + units,
        entitlement.window_allowance,
        state.month_units_used + units,
        (entitlement.monthly_backstop if entitlement.monthly_backstop is not None else "unbounded"),
    )

    return CreditConsumptionResult(
        user=updated,
        units_consumed=units,
        window_resets_at=state.resets_at,
        window_units_used=state.units_used + units,
        window_allowance=entitlement.window_allowance,
        warning=_warning(state, entitlement.window_allowance, state.units_used + units),
    )


async def record_units(user_id: str, units: int, operation: str = "unknown") -> None:
    """Advance the window and month counters by `units`. **Accounts, never refuses.**

    The distinction from `consume_credits` is deliberate and is the whole reason this exists as a
    second function rather than a flag. `consume_credits` is a *gate*: it is called before an
    operation, with an amount known in advance, and it raises when the window cannot fund it. This
    is *accounting*: it is called after a generation that has already happened, with the amount it
    actually cost, and there is nothing left to refuse — the money is spent and the artefact exists.

    Decision L: **charge on success, absorb on failure.** A learner who has already received a
    lesson must not have it taken away because the meter noticed afterwards that they were over
    their allowance, so this records the overshoot and lets the *next* operation be refused by the
    gate. The consequence is that a window can be exceeded by at most the cost of one operation in
    flight, which is the price of measuring cost instead of estimating it.

    Failures are swallowed and logged at `error`. Nothing a meter does is worth losing a
    generation the learner already waited for.
    """
    if units <= 0:
        return
    try:
        identity_repo = IdentityRepository()
        fresh = await identity_repo.find_by_id(user_id)
        if not fresh:
            logger.error("usage: cannot record %d units, user %s not found", units, user_id)
            return
        state = window_state(fresh)
        await identity_repo.update(
            fresh.id,
            {
                "usageWindowStartedAt": state.started_at,
                "usageWindowUnitsUsed": state.units_used + units,
                "usageMonthStartedAt": state.month_started_at,
                "usageMonthUnitsUsed": state.month_units_used + units,
            },
        )
        logger.info(
            "usage: user=%s operation=%s units=%d window=%d month=%d (recorded)",
            fresh.id,
            operation,
            units,
            state.units_used + units,
            state.month_units_used + units,
        )
    except Exception:
        # Deliberately broad. This runs after a successful generation, and every failure mode here
        # — a lost connection, a row vanishing, a serialisation conflict — is a reason to
        # under-charge rather than a reason to fail the caller.
        logger.exception(
            "usage: failed to record %d units for user=%s operation=%s",
            units,
            user_id,
            operation,
        )


async def _consume_space_credits(
    user: User, units: int, operation: str, space_id: str
) -> CreditConsumptionResult:
    """Unchanged behaviour, moved into its own function so the personal path reads straight through.

    A pass holder working in a Space spends the Space's credits, not their pass, and neither the
    window nor the entitlement is involved (Decision F).
    """
    is_available, _ = await check_credit_availability(user, units, space_id=space_id)
    if not is_available:
        raise SubscriptionLimitError(
            message="Space credit limit exceeded.",
            detail=f"This operation requires {units} credits, which exceeds the space's limit.",
        )

    from sqlalchemy import update as sa_update

    from src.domains.learning_spaces.db_models import Space

    factory = get_session_factory()
    async with factory() as session:
        await session.execute(
            sa_update(Space).where(Space.id == space_id).values(credits=Space.credits + units)
        )
        await session.commit()

    logger.info("Consumed %d credits for space %s (operation: %s)", units, space_id, operation)
    now = datetime.now(UTC)
    return CreditConsumptionResult(
        user=user,
        units_consumed=units,
        window_resets_at=now,
        window_units_used=0,
        window_allowance=0,
        warning=None,
    )


async def _notify_limit_reached(user: User, state: WindowState) -> None:
    """Email a learner who has run out, at most once a day.

    **Deduped per day, not per window**, which is a deliberate deviation from the plan's "the
    dedupe key moves from period to window". A 5-hour window permits 4.8 windows a day, so a
    per-window key would mail a heavy free learner up to five times daily where the old behaviour was
    once a month. The in-app refusal already carries the reset time and is the right place to say
    "your allowance is back at 3:40 PM"; an email is for the learner who is not looking at the app,
    and that learner needs telling once.

    The log row is the dedupe key, so it may only be written for a send that actually happened. It
    used to be written unconditionally, and because the mailer swallows delivery failures a rejected
    send still marked the period as notified — observed 2026-08-31 with a Gmail `535 5.7.8`.
    """
    day_key = state.resets_at.replace(hour=0, minute=0, second=0, microsecond=0)
    try:
        factory = get_session_factory()
        async with factory() as session:
            existing = (
                await session.execute(
                    select(LimitReachedEmailLog).where(
                        LimitReachedEmailLog.user_id == user.id,
                        LimitReachedEmailLog.window_day == day_key,
                    )
                )
            ).scalar_one_or_none()
        if existing:
            return

        delivered = await send_limit_reached_email(email=user.email, name=user.name or None)
        if not delivered:
            logger.warning(
                "Limit-reached email was not delivered to user %s; not recording it as sent, so it "
                "will be retried on the next refusal today.",
                user.id,
            )
            return

        async with factory() as session:
            session.add(LimitReachedEmailLog(user_id=user.id, window_day=day_key))
            await session.commit()
    except Exception as e:
        logger.warning("Failed to send limit reached email to %s: %s", user.id, e)


# ===========================================================================
# Reads
# ===========================================================================


async def get_credit_usage(user: User, db_client: Any | None = None) -> dict:
    """What the learner is shown: a percentage and a reset time, never a unit count.

    Units are a COGS accounting device and mean nothing to a learner; a raw number invites exactly
    the arithmetic we do not want them doing, and it would leak our cost basis into the UI. The
    marketing states concrete equivalents instead — "about 15 minutes of live voice tutoring" — which
    is checkable and true.

    `monthlyPercentUsed` appears only above 80% of the backstop, because a limit a learner never
    reaches is better not mentioned: naming it invites them to plan around a number that was designed
    not to bind.
    """
    from src.domains.billing.services import entitlement_service

    identity_repo = IdentityRepository()
    fresh = await identity_repo.find_by_id(user.id)
    if not fresh:
        raise ValueError(f"User {user.id} not found")

    entitlement = await entitlement_service.resolve(fresh.id)
    state = window_state(fresh)
    allowance = entitlement.window_allowance

    percent_used = round(min(100.0, state.units_used / allowance * 100), 1) if allowance else 0.0

    result: dict[str, Any] = {
        "tier": entitlement.tier,
        "windowResetsAt": state.resets_at.isoformat(),
        "percentUsed": percent_used,
        "isExhausted": allowance > 0 and state.units_used >= allowance,
    }

    backstop = entitlement.monthly_backstop
    if backstop:
        monthly_percent = min(100.0, state.month_units_used / backstop * 100)
        # `monthlyExhausted` is reported whether or not the percentage is, because a refusal has to
        # know which limit bound it: the backstop's remedy is not "wait five hours". The *percentage*
        # stays hidden below the disclosure threshold; the boolean is only ever true at the point
        # where hiding it would mean refusing without saying why.
        result["monthlyExhausted"] = state.month_units_used >= backstop
        if monthly_percent >= BACKSTOP_DISCLOSURE_FRACTION * 100:
            result["monthlyPercentUsed"] = round(monthly_percent, 1)

    return result
