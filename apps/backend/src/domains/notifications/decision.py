"""The deterministic decision engine: one place that decides when, where, and how a notification
is sent, and records why.

This is Level 0 of the intelligence layer from the plan, and it is the control and fallback
forever. It makes exactly the decisions the orchestrator already made inline — quiet-hours and
attention-budget timing, which channels to plan, whether a notification groups — but as one
auditable function that emits reason codes instead of scattered `if` branches. Centralising them
here is what lets a later statistical or LLM layer *propose* a different decision and be compared
against this baseline, and fall back to it when it is unavailable or unsafe.

Three rules bound everything here, from the plan's Decision E and section 7.1, and they are why the
engine takes its consent and budget inputs rather than computing them:

  1. It never expands the attention budget. It receives `allowance_spent` and honours it.
  2. It never relaxes consent. Channel consent is resolved by the service and rechecked at
     dispatch; the engine only chooses among channels it is told are permitted.
  3. It never sends in quiet hours. Quiet hours defer, and they outrank the budget, exactly as
     before.

At Level 0 the engine is purely a function of its inputs and its output is identical to the
previous inline logic, so recording a decision changes nothing a learner can observe. That is
deliberate: the record is the audit trail and the substrate for outcome attribution, and shadow
mode has nothing to diverge from yet.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from src.shared.time import (
    LearnerTimezone,
    is_within_quiet_hours,
    local_day_bounds,
    next_end_of_quiet_hours,
)

#: Bumped when the deterministic rules change, so a stored decision can be read against the logic
#: that produced it. Model-driven layers will set `model_version` alongside this instead.
POLICY_VERSION = "deterministic-v1"

#: Priority at or below this bypasses the attention budget. Mirrors service.PRIORITY_TIME_CRITICAL;
#: kept as its own constant so the engine has no import cycle back into the service.
PRIORITY_TIME_CRITICAL = 1

#: In-app is always planned — the Notification row itself is the in-app delivery — so it is never a
#: decision, but it is recorded so the channel set in the audit trail is complete.
_IN_APP = "IN_APP"


@dataclass(frozen=True)
class DecisionInput:
    """Everything the engine needs, resolved by the service so the engine stays pure and testable.

    The consent and budget facts arrive already decided (`email_planned`, `allowance_spent`)
    precisely so the engine cannot overrule them.
    """

    notification_type: str
    priority: int
    urgency: str
    category: str | None
    intelligence_scope: str
    moment: datetime
    timezone: LearnerTimezone
    quiet_from: object  # datetime.time | None — opaque here; passed straight to the time helpers
    quiet_to: object
    allowance_spent: bool
    default_channels: tuple[str, ...]
    allowed_channels: tuple[str, ...]
    email_planned: bool
    group_key: str | None
    groupable: bool


@dataclass(frozen=True)
class NotificationDecisionRecord:
    """One decision, in the shape the audit table stores.

    Built by the service and handed to the repository so the record is written inside the same
    transaction as the notification it explains — the FK links atomically, and a replayed create
    cannot leave an orphan decision behind. The engine produces the content; this is only the
    transport, deliberately free of SQLAlchemy so the pure engine stays pure.
    """

    user_id: str
    notification_type: str
    policy_version: str
    input_snapshot: dict
    decision: dict
    reason_codes: list[str]
    used_fallback: bool
    #: Reserved for the statistical and LLM layers; unset at Level 0.
    model_version: str | None = None
    confidence: float | None = None
    experiment_id: str | None = None


@dataclass(frozen=True)
class PlanDecision:
    """What the engine decided, in the terms the orchestrator and the audit record both need."""

    status: str  # "PENDING" (send now) or "QUEUED" (deferred to eligible_at)
    eligible_at: datetime
    channels: tuple[str, ...]
    reason_codes: tuple[str, ...]

    def input_snapshot(self, decision_input: DecisionInput) -> dict:
        """The facts the decision was made from, for the audit record.

        Deliberately excludes anything sensitive: no titles, bodies, or entity ids — only the
        policy inputs. The timezone is recorded by name, not object.
        """

        return {
            "notificationType": decision_input.notification_type,
            "priority": decision_input.priority,
            "urgency": decision_input.urgency,
            "category": decision_input.category,
            "intelligenceScope": decision_input.intelligence_scope,
            "moment": decision_input.moment.isoformat(),
            "timezone": decision_input.timezone.name,
            "quietHours": bool(decision_input.quiet_from and decision_input.quiet_to),
            "allowanceSpent": decision_input.allowance_spent,
            "defaultChannels": list(decision_input.default_channels),
            "allowedChannels": list(decision_input.allowed_channels),
            "emailPlanned": decision_input.email_planned,
            "grouped": decision_input.group_key is not None,
        }

    def decision_record(self) -> dict:
        """The chosen plan, for the audit record's `decision` column."""

        return {
            "status": self.status,
            "eligibleAt": self.eligible_at.isoformat(),
            "channels": list(self.channels),
        }


def _timing(decision_input: DecisionInput) -> tuple[str, datetime, list[str]]:
    """Decide send-now versus deferred, and to when. Mirrors the prior inline block exactly.

    Order matters and is load-bearing: quiet hours outranks the budget, so a time-critical message
    still waits for morning (a 3am push helps no one), while a time-critical message that is merely
    over budget goes now.
    """

    moment = decision_input.moment
    if is_within_quiet_hours(
        moment, decision_input.timezone, decision_input.quiet_from, decision_input.quiet_to
    ):
        return (
            "QUEUED",
            next_end_of_quiet_hours(moment, decision_input.timezone, decision_input.quiet_to),
            ["QUIET_HOURS_DEFERRED"],
        )
    if decision_input.priority > PRIORITY_TIME_CRITICAL and decision_input.allowance_spent:
        # Deferred to the start of the learner's next local day, where the budget resets.
        _, next_day = local_day_bounds(moment, decision_input.timezone)
        return "QUEUED", next_day, ["ATTENTION_BUDGET_DEFERRED"]
    if decision_input.priority <= PRIORITY_TIME_CRITICAL and decision_input.allowance_spent:
        # Sent now despite a spent budget, because it is time-critical — recorded so the audit
        # trail shows the bypass was a decision, not budget miscounting.
        return "PENDING", moment, ["TIME_CRITICAL_BYPASSES_BUDGET"]
    return "PENDING", moment, ["IMMEDIATE"]


def _channels(decision_input: DecisionInput) -> tuple[tuple[str, ...], list[str]]:
    """Decide which channels to plan, within the type's allowed ceiling.

    At Level 0 this is exactly the create-time planning the service did: in-app always, mobile and
    web push when the type defaults to them, email when consent resolved to a plan. It is expressed
    here as an intersection with `allowed_channels` so that a later ranker adding a channel cannot
    exceed the taxonomy's ceiling — today the intersection is a no-op because defaults are a subset
    of allowed.
    """

    channels: list[str] = [_IN_APP]
    reasons: list[str] = []
    for channel in ("MOBILE_PUSH", "WEB_PUSH"):
        if (
            channel in decision_input.default_channels
            and channel in decision_input.allowed_channels
        ):
            channels.append(channel)
    if decision_input.email_planned:
        # Only reached when the service already resolved consent, suppression, and a usable
        # address, so this records a consented decision rather than making one.
        if "EMAIL" in decision_input.allowed_channels:
            channels.append("EMAIL")
            reasons.append("EMAIL_CONSENTED")
    if decision_input.group_key is not None and decision_input.groupable:
        reasons.append("GROUPED")
    return tuple(channels), reasons


def decide(decision_input: DecisionInput) -> PlanDecision:
    """Produce the deterministic plan for one notification.

    Pure: the same inputs always give the same decision, which is what makes the baseline a stable
    control to measure a learned layer against.
    """

    status, eligible_at, timing_reasons = _timing(decision_input)
    channels, channel_reasons = _channels(decision_input)
    return PlanDecision(
        status=status,
        eligible_at=eligible_at,
        channels=channels,
        reason_codes=tuple(timing_reasons + channel_reasons),
    )


# --------------------------------------------------------------------------- shadow mode


@dataclass(frozen=True)
class ResolvedDecision:
    """Which decision actually took effect, and how it relates to what was proposed.

    The deterministic baseline is the control and the fallback forever. A learned layer, when it
    exists, proposes an alternative; this is where the two meet. In shadow mode the proposal is
    recorded but never applied, so a model can be measured against live traffic for as long as it
    takes to trust it, at zero risk to the learner. Only when shadow mode is switched off does a
    proposal take effect — and even then never beyond what the baseline already permits, because
    the proposal is validated against the same hard constraints (that guard lands with the first
    real proposer, in the learned-ranking phase).

    At Level 0 there is no proposer, so `applied` is always the baseline and `mode` is
    `BASELINE_ONLY`. This type exists now so the audit trail can already distinguish an
    authoritative decision from an observed one the day a proposer is added.
    """

    applied: PlanDecision
    baseline: PlanDecision
    proposal: PlanDecision | None
    mode: str  # BASELINE_ONLY | SHADOW | LIVE
    divergences: tuple[str, ...]
    used_fallback: bool

    def reason_codes(self) -> list[str]:
        """The applied plan's reasons, plus the mode and any divergence, for the audit record."""

        codes = list(self.applied.reason_codes)
        codes.append(f"MODE_{self.mode}")
        codes.extend(self.divergences)
        return codes


def compare_decisions(baseline: PlanDecision, proposal: PlanDecision) -> tuple[str, ...]:
    """Name the ways a proposal differs from the baseline, for the shadow-mode audit.

    Divergence is the whole point of shadow mode: it is the record of what a learned layer *would*
    have changed, which is what later tells us whether the change was worth trusting. Reported as
    coarse codes rather than diffs so the audit stays low-cardinality and comparable across types.
    """

    divergences: list[str] = []
    if baseline.status != proposal.status or baseline.eligible_at != proposal.eligible_at:
        divergences.append("DIVERGE_TIMING")
    if set(baseline.channels) != set(proposal.channels):
        divergences.append("DIVERGE_CHANNELS")
    return tuple(divergences)


def resolve(
    baseline: PlanDecision,
    *,
    proposal: PlanDecision | None = None,
    shadow_only: bool = True,
) -> ResolvedDecision:
    """Decide what actually takes effect, given the baseline and an optional proposal.

    Three cases, and the default is the safe one:

      - no proposal: the baseline is applied; this is Level 0 and every notification today.
      - a proposal under shadow mode: the baseline is still applied, the proposal is recorded
        alongside it, and their divergence is captured. Nothing a learner sees changes.
      - a proposal with shadow mode off: the proposal is applied. (Its validation against hard
        constraints arrives with the first real proposer; there is none to validate yet.)
    """

    if proposal is None:
        return ResolvedDecision(
            applied=baseline,
            baseline=baseline,
            proposal=None,
            mode="BASELINE_ONLY",
            divergences=(),
            used_fallback=True,
        )
    divergences = compare_decisions(baseline, proposal)
    if shadow_only:
        return ResolvedDecision(
            applied=baseline,
            baseline=baseline,
            proposal=proposal,
            mode="SHADOW",
            divergences=divergences,
            used_fallback=True,
        )
    return ResolvedDecision(
        applied=proposal,
        baseline=baseline,
        proposal=proposal,
        mode="LIVE",
        divergences=divergences,
        used_fallback=False,
    )
