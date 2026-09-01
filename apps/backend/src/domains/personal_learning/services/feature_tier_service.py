"""
Feature Tier Service — The commercial capability layer for Personal Learning.

Defines what FREE and PLUS users can access. The principle is:
- FREE gets all features at a genuinely useful basic level.
- PLUS enhances quality, depth, modes, and intelligence.
- No feature is entirely locked behind a paywall.

This service is the single source of truth for all tier-based gate checks.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Literal

logger = logging.getLogger(__name__)


# ===========================================================================
# Feature Tier Matrix — configurable constant (no DB, fast iteration)
# ===========================================================================

FEATURE_TIER_MATRIX: dict[str, dict[str, Any]] = {
    "flashcard_generation": {
        "free": {
            "max_per_note": 5,
            "types": ["basic_qa"],
            "description": "Generate up to 5 Q&A flashcards per note",
        },
        "plus": {
            "max_per_note": 10,
            "types": ["basic_qa", "cloze", "multi_choice", "image_prompt"],
            "description": "Generate up to 10 flashcards per note with varied question types",
        },
        "upgrade_value": "Get 2x more flashcards per note with cloze, multiple-choice, and image-based cards",
    },
    "course_creation": {
        # A quota rather than a set of permitted values, which is why `check_capability` does not gate it —
        # that function answers "may this learner use this mode/format/style", and a count is a different
        # question. The entry exists so the limit is described in one place with the others, and so the
        # capabilities summary endpoint tells the truth about it instead of omitting it.
        #
        # `knowledge.course_service.ensure_can_create_course` reads `max_per_month` and `upgrade_value` from
        # here, so changing the number changes the enforcement and the message together.
        "free": {
            "max_per_month": 2,
            "description": "Create up to 2 courses each month",
        },
        "plus": {
            "max_per_month": None,
            "description": "Create as many courses as you want",
        },
        "upgrade_value": "Build unlimited courses, with lessons written for each topic as you reach it",
    },
    "quiz_modes": {
        "free": {
            "modes": ["FULL_PRACTICE", "WEAK_AREAS", "TOPIC_FOCUS"],
            "description": "Practice quizzes with standard, weak areas, and topic focus modes",
        },
        "plus": {
            "modes": [
                "FULL_PRACTICE",
                "WEAK_AREAS",
                "TOPIC_FOCUS",
                "PAST_PAPER_SIM",
                "ADAPTIVE",
            ],
            "description": "All quiz modes including past paper simulation and adaptive difficulty",
        },
        "upgrade_value": "Unlock adaptive quizzes that adjust difficulty based on your performance, and past paper simulation mode",
    },
    "document_generation": {
        "free": {
            "formats": ["pdf"],
            "styles": ["academic"],
            "description": "Generate PDF documents in academic style",
        },
        "plus": {
            "formats": ["pdf", "docx", "pptx"],
            "styles": ["academic", "report", "minimal"],
            "description": "Generate PDF, DOCX, and PPTX in academic, report, and minimal styles",
        },
        "upgrade_value": "Generate documents in DOCX and PPTX formats with report and minimal styles",
    },
    "study_plan": {
        "free": {
            "adaptive": False,
            "description": "AI-generated study plans with timeline distribution",
        },
        "plus": {
            "adaptive": True,
            "description": "Adaptive study plans that adjust based on quiz performance and behaviour",
        },
        "upgrade_value": "Get study plans that adapt automatically based on your quiz scores and study patterns",
    },
    "reflection": {
        "free": {
            "depth": "summary",
            "description": "Weekly activity summaries showing what you did",
        },
        "plus": {
            "depth": "deep_analysis",
            "description": "Deep reflections with cross-topic pattern analysis and actionable recommendations",
        },
        "upgrade_value": "Get deeper insights that identify patterns across topics and provide specific next steps",
    },
    "behaviour_analytics": {
        "free": {
            "features": ["basic_profile"],
            "description": "See your basic study patterns and consistency",
        },
        "plus": {
            "features": [
                "basic_profile",
                "predictive_scheduling",
                "optimal_time_suggestions",
                "dropout_prevention",
            ],
            "description": "Predictive scheduling, optimal time suggestions, and proactive dropout prevention",
        },
        "upgrade_value": "Get AI-powered predictions about your best study times and proactive support when your consistency drops",
    },
}

# Capabilities that are entirely PLUS-only (not just enhanced)
PLUS_ONLY_CAPABILITIES = {
    "PAST_PAPER_SIM",  # Quiz mode
    "ADAPTIVE",  # Quiz mode
    "predictive_scheduling",  # Behaviour analytics feature
    "optimal_time_suggestions",  # Behaviour analytics feature
    "dropout_prevention",  # Behaviour analytics feature
}


# ===========================================================================
# Data Classes
# ===========================================================================


@dataclass
class CapabilityAllowed:
    """User can access this capability."""

    allowed: bool = True
    tier: str = "free"
    is_trial: bool = False
    trial_days_remaining: int | None = None
    capability_spec: dict[str, Any] | None = None


@dataclass
class CapabilityDenied:
    """User cannot access this capability — upgrade required."""

    allowed: bool = False
    reason: str = ""
    capability: str = ""
    upgrade_url: str = "/subscription"
    trial_available: bool = False
    upgrade_value: str = ""


@dataclass
class CapabilityEntry:
    """A single capability in the summary."""

    id: str
    name: str
    free_description: str
    plus_description: str
    user_level: str  # "free" or "plus"
    locked_features: list[str] | None = None
    upgrade_value: str = ""


@dataclass
class CapabilitiesSummary:
    """Full capability summary for a user."""

    effective_tier: str  # "free" or "plus"
    is_trial: bool
    trial_days_remaining: int | None
    capabilities: list[CapabilityEntry]


# ===========================================================================
# Service Functions
# ===========================================================================


async def get_effective_tier(
    user_id: str,
) -> tuple[Literal["free", "plus"], bool, int | None]:
    """
    Get the effective feature tier for a user.

    Returns (tier, is_trial, trial_days_remaining).

    A thin caller of `entitlement_service.resolve`, which is the single thing that decides whether a
    learner is Plus (Decision B). The three-tuple shape is kept because ~15 call sites across this
    module, `knowledge`, `conversion_engine` and `routes` read it, and none of them needs to change
    to gain passes: a pass holder arrives here as `("plus", False, None)`, exactly like a subscriber.

    What this used to do, and why it was wrong: it read `User.tier` itself and matched
    `startswith("PREMIUM")`, which denied every capability to `STUDY_CIRCLE_*` and `SQUAD_*` tiers
    (drift 10) while the credit meter granted them millions of credits. Two reads, one prefix match,
    and an answer that disagreed with three other mechanisms.
    """
    from src.domains.billing.services import entitlement_service

    entitlement = await entitlement_service.resolve(user_id)
    return entitlement.tier, entitlement.is_trial, entitlement.trial_days_remaining


async def check_capability(
    user_id: str,
    capability: str,
    *,
    requested_value: str | None = None,
) -> CapabilityAllowed | CapabilityDenied:
    """
    Check if a user can access a specific capability or feature value.

    Args:
        user_id: The user's ID
        capability: The capability key from FEATURE_TIER_MATRIX
        requested_value: Optional specific value being requested (e.g., "PAST_PAPER_SIM" for quiz_modes)

    Returns:
        CapabilityAllowed if user can access, CapabilityDenied otherwise.
    """
    tier, is_trial, trial_days = await get_effective_tier(user_id)

    # If user is PLUS (subscription or trial), always allowed
    if tier == "plus":
        cap_spec = FEATURE_TIER_MATRIX.get(capability, {}).get("plus")
        return CapabilityAllowed(
            tier=tier,
            is_trial=is_trial,
            trial_days_remaining=trial_days,
            capability_spec=cap_spec,
        )

    # User is FREE — check if the requested value is gated
    matrix_entry = FEATURE_TIER_MATRIX.get(capability)
    if not matrix_entry:
        # Unknown capability — allow by default (fail open for ungated features)
        return CapabilityAllowed(tier="free")

    free_spec = matrix_entry.get("free", {})
    plus_spec = matrix_entry.get("plus", {})
    upgrade_value = matrix_entry.get("upgrade_value", "")

    # If a specific value was requested, check if it's available at free tier
    if requested_value:
        # Check in modes list
        free_modes = free_spec.get("modes", [])
        plus_modes = plus_spec.get("modes", [])
        if plus_modes and requested_value in plus_modes and requested_value not in free_modes:
            return CapabilityDenied(
                reason=f"'{requested_value}' mode requires Maigie Plus",
                capability=capability,
                upgrade_url="/subscription",
                trial_available=await _trial_available(user_id),
                upgrade_value=upgrade_value,
            )

        # Check in formats list
        free_formats = free_spec.get("formats", [])
        plus_formats = plus_spec.get("formats", [])
        if plus_formats and requested_value in plus_formats and requested_value not in free_formats:
            return CapabilityDenied(
                reason=f"'{requested_value}' format requires Maigie Plus",
                capability=capability,
                upgrade_url="/subscription",
                trial_available=await _trial_available(user_id),
                upgrade_value=upgrade_value,
            )

        # Check in styles list
        free_styles = free_spec.get("styles", [])
        plus_styles = plus_spec.get("styles", [])
        if plus_styles and requested_value in plus_styles and requested_value not in free_styles:
            return CapabilityDenied(
                reason=f"'{requested_value}' style requires Maigie Plus",
                capability=capability,
                upgrade_url="/subscription",
                trial_available=await _trial_available(user_id),
                upgrade_value=upgrade_value,
            )

        # Check in features list
        free_features = free_spec.get("features", [])
        plus_features = plus_spec.get("features", [])
        if (
            plus_features
            and requested_value in plus_features
            and requested_value not in free_features
        ):
            return CapabilityDenied(
                reason=f"'{requested_value}' requires Maigie Plus",
                capability=capability,
                upgrade_url="/subscription",
                trial_available=await _trial_available(user_id),
                upgrade_value=upgrade_value,
            )

        # Check boolean flags (e.g., adaptive)
        if requested_value == "adaptive" and not free_spec.get("adaptive", False):
            if plus_spec.get("adaptive", False):
                return CapabilityDenied(
                    reason="Adaptive study plans require Maigie Plus",
                    capability=capability,
                    upgrade_url="/subscription",
                    trial_available=await _trial_available(user_id),
                    upgrade_value=upgrade_value,
                )

    # No specific gated value requested — allowed at free level
    return CapabilityAllowed(tier="free", capability_spec=free_spec)


async def get_quality_tier(user_id: str) -> Literal["free", "plus"]:
    """Get the quality tier for AI generation operations."""
    tier, _, _ = await get_effective_tier(user_id)
    return tier


async def get_capabilities_summary(user_id: str) -> CapabilitiesSummary:
    """
    Return the full capability summary for the /capabilities endpoint.

    Shows what the user can access and what's locked with value descriptions.
    """
    tier, is_trial, trial_days = await get_effective_tier(user_id)

    capabilities = []
    for cap_id, matrix in FEATURE_TIER_MATRIX.items():
        free_spec = matrix.get("free", {})
        plus_spec = matrix.get("plus", {})
        upgrade_value = matrix.get("upgrade_value", "")

        # Determine what's locked for this user
        locked_features = None
        if tier == "free":
            locked_features = _get_locked_features(free_spec, plus_spec)

        entry = CapabilityEntry(
            id=cap_id,
            name=_capability_display_name(cap_id),
            free_description=free_spec.get("description", ""),
            plus_description=plus_spec.get("description", ""),
            user_level=tier,
            locked_features=locked_features,
            upgrade_value=upgrade_value,
        )
        capabilities.append(entry)

    return CapabilitiesSummary(
        effective_tier=tier,
        is_trial=is_trial,
        trial_days_remaining=trial_days,
        capabilities=capabilities,
    )


# ===========================================================================
# Helpers
# ===========================================================================


async def trial_available(user_id: str) -> bool:
    """Whether this learner may still start a trial.

    Public because gates outside this domain need it to build an upgrade payload — the course cap in
    `knowledge` is one. Without it a `403` could not say whether a trial was on offer, and the panel would
    either omit that or guess.
    """
    return await _trial_available(user_id)


async def _trial_available(user_id: str) -> bool:
    """Check if the user is eligible to start a trial (cooldown since last trial has elapsed).

    Reads `trial_service.TRIAL_COOLDOWN_DAYS` rather than repeating the number, so this
    check and `trial_service.start_trial`'s own rejection cannot disagree — a learner is
    never told a trial is available only to have `start_trial` refuse it.
    """
    from src.domains.personal_learning.repository import PersonalLearningRepository

    from . import trial_service

    repo = PersonalLearningRepository()
    profile = await repo.get_profile_by_user(user_id)
    if not profile:
        return True  # New user can trial
    if profile.last_trial_ended_at:
        days_since = (datetime.now(UTC) - profile.last_trial_ended_at).days
        return days_since >= trial_service.TRIAL_COOLDOWN_DAYS
    if profile.trial_started_at:
        return False  # Currently on trial or just finished
    return True


def _get_locked_features(free_spec: dict, plus_spec: dict) -> list[str]:
    """Determine what features in plus_spec are not available in free_spec."""
    locked = []

    # Compare mode lists
    free_modes = set(free_spec.get("modes", []))
    plus_modes = set(plus_spec.get("modes", []))
    for mode in plus_modes - free_modes:
        locked.append(f"mode:{mode}")

    # Compare format lists
    free_formats = set(free_spec.get("formats", []))
    plus_formats = set(plus_spec.get("formats", []))
    for fmt in plus_formats - free_formats:
        locked.append(f"format:{fmt}")

    # Compare style lists
    free_styles = set(free_spec.get("styles", []))
    plus_styles = set(plus_spec.get("styles", []))
    for style in plus_styles - free_styles:
        locked.append(f"style:{style}")

    # Compare feature lists
    free_features = set(free_spec.get("features", []))
    plus_features = set(plus_spec.get("features", []))
    for feat in plus_features - free_features:
        locked.append(f"feature:{feat}")

    # Compare boolean flags
    if plus_spec.get("adaptive") and not free_spec.get("adaptive"):
        locked.append("adaptive_scheduling")

    # Compare depth
    if plus_spec.get("depth") and plus_spec.get("depth") != free_spec.get("depth"):
        locked.append(f"depth:{plus_spec['depth']}")

    # Compare limits
    free_max = free_spec.get("max_per_note")
    plus_max = plus_spec.get("max_per_note")
    if free_max and plus_max and plus_max > free_max:
        locked.append(f"limit:max_per_note={plus_max}")

    return locked if locked else []


def _capability_display_name(cap_id: str) -> str:
    """Convert capability ID to human-friendly name."""
    names = {
        "flashcard_generation": "Flashcard Generation",
        "quiz_modes": "Quiz Modes",
        "document_generation": "Document Generation",
        "study_plan": "Study Plans",
        "reflection": "Reflections",
        "behaviour_analytics": "Behaviour Analytics",
    }
    return names.get(cap_id, cap_id.replace("_", " ").title())
