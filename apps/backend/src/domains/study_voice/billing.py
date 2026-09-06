"""Time-based billing for a voice session.

Every other paid operation is priced from the tokens a generation actually used. Voice is priced by
**time connected**, because that is what Gemini Live charges for, and this module is the only place
that arithmetic lives.

Ported from `gemini_live_service.py` at `4953972^` with the logic unchanged. It is worth stating what
that logic is, because the eight `GEMINI_LIVE_*` settings look self-explanatory and are not:

- **Two billing modes, chosen by tier.** A paid learner is billed only for `active_audio` — time when
  either they or the tutor were recently speaking. A FREE learner is billed `wall_clock`, the whole
  time the socket is open. That asymmetry is deliberate: standby is a perk, not a default.
- **A session floor, for wall-clock only.** `GEMINI_LIVE_MIN_SESSION_SECONDS` is charged as a minimum
  at settlement for FREE sessions, so a thirty-second connection is not free. It is *not* applied to
  `active_audio`, where a short session genuinely cost little.
- **Accrual is separate from settlement.** `chargeable_seconds_raw` is the running total during the
  session; `chargeable_seconds_final_settlement` is the close-out. Keeping them separate is what stops
  the final flush from double-charging what the ticks already took.

**Voice is charged in seconds, against its own balance, and no longer in usage units (§6.3).** Every
figure in this module used to be converted into units at `GEMINI_LIVE_UNITS_PER_MINUTE` and deducted
from the 5-hour window — which meant a voice minute and a chat turn competed for one allowance at a
40× cost ratio, so the allowance had to be priced for the voice case and was spent almost entirely on
the text case. `voiceSecondsRemaining` holds the balance now, and the conversion is gone: what this
module computes is seconds, what `voice_service.spend` takes is seconds, and there is no rate in
between to be wrong.

That absence is the point rather than a tidy-up. **The same rate has now been wrong in both
directions** — 100 units/minute under-priced voice by ~100× for the life of the feature, and the
batching threshold derived from it was 0.3 seconds of audio when it was meant to be 15. Both were
arithmetic on a derived currency, and neither was visible as a wrong number.

`GEMINI_LIVE_UNITS_PER_MINUTE` survives as a **cost basis** — 200 units, $0.02 a minute, against a
measured ~230, erring towards the learner. The margin tables need it and it is what makes "40× a chat
turn" checkable. Nothing charges with it.
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
    """What a voice minute *costs*, in §6.2 units. No longer what it is charged in.

    Voice draws from `voiceSecondsRemaining` now (§6.3), so nothing converts time into units to bill
    it. This is retained as the cost basis: the margin tables need it, and it is what makes "a voice
    minute is 40× a chat turn" checkable.
    """
    return float(get_settings().GEMINI_LIVE_UNITS_PER_MINUTE)


def min_session_seconds() -> int:
    """The wall-clock session floor, in seconds. Not applied to `active_audio`."""
    return int(get_settings().GEMINI_LIVE_MIN_SESSION_SECONDS)


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


def billing_min_consume_seconds() -> int:
    """Batch database writes until the accrual reaches this many seconds, or the flush interval elapses.

    Without it, a 2-second tick would mean a write every 2 seconds per active session.
    """
    return int(get_settings().GEMINI_LIVE_BILLING_MIN_CONSUME_SECONDS)


def billing_flush_interval_seconds() -> float:
    return float(get_settings().GEMINI_LIVE_BILLING_FLUSH_INTERVAL_SECONDS)


def chargeable_seconds_raw(billable_seconds: float) -> int:
    """Running chargeable total for the time billed so far. No session floor.

    Now the identity function on a whole number of seconds, and that is the point: voice is billed by
    time against a balance held in seconds, so there is no conversion to get wrong. What this replaced
    — `units_from_billable_seconds_raw` — multiplied by a rate that had been two orders of magnitude
    wrong for the life of the feature, and the multiplication is what hid it.
    """
    return int(max(0.0, billable_seconds))


def chargeable_seconds_final_settlement(billable_seconds: float, billing_mode: str) -> int:
    """Final chargeable total once the session has ended.

    The floor applies to `wall_clock` only — see the module docstring.
    """
    raw = chargeable_seconds_raw(billable_seconds)
    if billing_mode == BILLING_ACTIVE_AUDIO:
        return raw
    return max(min_session_seconds(), raw)
