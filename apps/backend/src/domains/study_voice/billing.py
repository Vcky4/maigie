"""Time-based billing for a voice session.

Every other paid operation is priced from the tokens a generation actually used. Voice is priced by
**time connected**, because that is what Gemini Live charges for, and this module is the only place
that arithmetic lives.

Ported from `gemini_live_service.py` at `4953972^` with the logic unchanged. It is worth stating what
that logic is, because the eight `GEMINI_LIVE_*` settings look self-explanatory and are not:

- **Two billing modes, chosen by tier.** A paid learner is billed only for `active_audio` — time when
  either they or the tutor were recently speaking. A FREE learner is billed `wall_clock`, the whole
  time the socket is open. That asymmetry is deliberate: standby is a perk, not a default.
- **A session floor, for wall-clock only.** `GEMINI_LIVE_MIN_SESSION_UNITS` is charged as a minimum
  at settlement for FREE sessions, so a thirty-second connection is not free. It is *not* applied to
  `active_audio`, where a short session genuinely cost little.
- **Accrual is separate from settlement.** `units_from_billable_seconds_raw` is the running total
  during the session; `units_total_final_settlement` is the close-out. Keeping them separate is what
  stops the final flush from double-charging what the ticks already took.

**Everything here is now denominated in usage units — $0.0001 of measured cost (§6.2) — and there is
no multiplier.** The module used to describe its figures as "pre-multiplier", because `consume_credits`
applied `TOKEN_MULTIPLIER = 0.2` to whatever it was handed, so passing 100 charged 20. That indirection
is gone: what a caller passes is what is charged, and it is expressed in the same unit as every other
operation, which is what makes "a voice minute costs 200 and a chat turn costs 29" a comparison a
reader can actually make.

The rate is 200 units per minute against a measured ~230, erring towards the learner. See
`Settings.GEMINI_LIVE_UNITS_PER_MINUTE` for how the 100× under-pricing this replaced came about, and
for why it is a lesson about where cost bugs hide rather than a footnote.
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


def units_per_minute() -> float:
    return float(get_settings().GEMINI_LIVE_UNITS_PER_MINUTE)


def min_session_units() -> int:
    """The wall-clock session floor. Not applied to `active_audio`."""
    return int(get_settings().GEMINI_LIVE_MIN_SESSION_UNITS)


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

    Without it, a 2-second tick would mean a usage write every 2 seconds per active session.
    """
    return int(get_settings().GEMINI_LIVE_BILLING_MIN_CONSUME_CHUNK)


def billing_flush_interval_seconds() -> float:
    return float(get_settings().GEMINI_LIVE_BILLING_FLUSH_INTERVAL_SECONDS)


def units_from_billable_seconds_raw(billable_seconds: float) -> int:
    """Running unit total for the time billed so far. No session floor."""
    return int(max(0.0, billable_seconds) / 60.0 * units_per_minute())


def units_total_final_settlement(billable_seconds: float, billing_mode: str) -> int:
    """Final unit total once the session has ended.

    The floor applies to `wall_clock` only — see the module docstring.
    """
    raw = units_from_billable_seconds_raw(billable_seconds)
    if billing_mode == BILLING_ACTIVE_AUDIO:
        return raw
    return max(min_session_units(), raw)
