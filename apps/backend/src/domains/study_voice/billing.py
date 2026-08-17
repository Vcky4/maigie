"""Time-based billing for a voice session.

Every other paid operation in the product is billed by tokens. Voice is billed by **time connected**, and
this module is the only place that arithmetic lives.

Ported from `gemini_live_service.py` at `4953972^` with the logic unchanged. It is worth stating what that
logic is, because the eight `GEMINI_LIVE_*` settings look self-explanatory and are not:

- **Two billing modes, chosen by tier.** A paid learner is billed only for `active_audio` — time when
  either they or the tutor were recently speaking. A FREE learner is billed `wall_clock`, the whole time
  the socket is open. That asymmetry is deliberate: standby is a perk, not a default.
- **A session floor, for wall-clock only.** `GEMINI_LIVE_MIN_SESSION_CREDITS` is charged as a minimum at
  settlement for FREE sessions, so a thirty-second connection is not free. It is *not* applied to
  `active_audio`, where a short session genuinely cost little.
- **Accrual is separate from settlement.** `credits_from_billable_seconds_raw` is the running total during
  the session; `credits_total_final_settlement` is the close-out. Keeping them separate is what stops the
  final flush from double-charging what the ticks already took.

All figures here are **pre-multiplier**. `consume_credits` applies `TOKEN_MULTIPLIER` itself, so a caller
passing 100 credits charges 20. That is the existing convention for every other operation and voice
follows it rather than being special — but it does mean `GEMINI_LIVE_CREDITS_PER_MINUTE = 100` bills 20
effective credits per minute, which is a pricing question flagged in the design document, not a bug here.
"""

from __future__ import annotations

from src.config import get_settings

#: Billing modes. `wall_clock` bills all connected time; `active_audio` bills only recent speech.
BILLING_WALL_CLOCK = "wall_clock"
BILLING_ACTIVE_AUDIO = "active_audio"


def billing_mode_for_tier(tier: str | None) -> str:
    """Paid tiers bill only while audio is flowing; FREE bills wall clock.

    Kept as a function of tier rather than a constant so that unpaid standby stays a plan-level property.
    """
    return BILLING_ACTIVE_AUDIO if str(tier or "FREE") != "FREE" else BILLING_WALL_CLOCK


def credits_per_minute() -> float:
    return float(get_settings().GEMINI_LIVE_CREDITS_PER_MINUTE)


def min_session_credits() -> int:
    """The wall-clock session floor. Not applied to `active_audio`."""
    return int(get_settings().GEMINI_LIVE_MIN_SESSION_CREDITS)


def standby_idle_seconds() -> float:
    """No learner audio and no tutor audio for this long counts as standby."""
    return float(get_settings().GEMINI_LIVE_STANDBY_IDLE_SECONDS)


def abandoned_after_seconds() -> float:
    """Silence this long means the sitting is over, not paused.

    Two orders of magnitude above `standby_idle_seconds`, and answering a different question. Standby is
    "should this moment be billed"; this is "is anyone still here". A learner who walks away leaving the tab
    open otherwise holds a session open indefinitely — billed by the minute if they are on FREE, and never
    reaching the teardown that writes the note they asked for.
    """
    return float(get_settings().GEMINI_LIVE_ABANDONED_AFTER_SECONDS)


def billing_tick_seconds() -> float:
    return float(get_settings().GEMINI_LIVE_BILLING_TICK_SECONDS)


def billing_min_consume_chunk() -> int:
    """Batch database writes until the accrual reaches this, or the flush interval elapses.

    Without it, a 2-second tick would mean a credit write every 2 seconds per active session.
    """
    return int(get_settings().GEMINI_LIVE_BILLING_MIN_CONSUME_CHUNK)


def billing_flush_interval_seconds() -> float:
    return float(get_settings().GEMINI_LIVE_BILLING_FLUSH_INTERVAL_SECONDS)


def credits_from_billable_seconds_raw(billable_seconds: float) -> int:
    """Running pre-multiplier total for the time billed so far. No session floor."""
    return int(max(0.0, billable_seconds) / 60.0 * credits_per_minute())


def credits_total_final_settlement(billable_seconds: float, billing_mode: str) -> int:
    """Final pre-multiplier total once the session has ended.

    The floor applies to `wall_clock` only — see the module docstring.
    """
    raw = credits_from_billable_seconds_raw(billable_seconds)
    if billing_mode == BILLING_ACTIVE_AUDIO:
        return raw
    return max(min_session_credits(), raw)
