"""Deterministic, fail-closed rollout gates for notification capabilities."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from typing import Literal

from src.config import Settings, get_settings

NotificationCapability = Literal["MOBILE_PUSH", "EMAIL", "WEB_PUSH", "INTELLIGENCE"]


@dataclass(frozen=True)
class CapabilityGate:
    enabled: bool
    denylist: frozenset[str]
    allowlist: frozenset[str]
    internal_allowlist: frozenset[str]
    rollout_percent: int


def stable_user_cohort(user_id: str) -> int:
    """Return a stable bucket in ``[0, 99]`` without persisting user identifiers."""

    return int.from_bytes(hashlib.sha256(user_id.encode()).digest()[:8], "big") % 100


def capability_gate(
    capability: NotificationCapability, *, settings: Settings | None = None
) -> CapabilityGate:
    """Build the configured gate for one capability.

    Unknown capabilities are rejected by the type contract and runtime lookup rather
    than silently inheriting another channel's consent or rollout configuration.
    """

    config = settings or get_settings()
    prefixes = {
        "MOBILE_PUSH": "MOBILE_PUSH",
        "EMAIL": "NOTIFICATION_EMAIL",
        "WEB_PUSH": "WEB_PUSH",
        "INTELLIGENCE": "NOTIFICATION_INTELLIGENCE",
    }
    prefix = prefixes[capability]
    return CapabilityGate(
        enabled=bool(getattr(config, f"{prefix}_ENABLED")),
        denylist=frozenset(getattr(config, f"{prefix}_DENYLIST")),
        allowlist=frozenset(getattr(config, f"{prefix}_ALLOWLIST")),
        internal_allowlist=frozenset(getattr(config, f"{prefix}_INTERNAL_ALLOWLIST")),
        rollout_percent=max(0, min(100, int(getattr(config, f"{prefix}_ROLLOUT_PERCENT")))),
    )


def capability_enabled_for(
    capability: NotificationCapability,
    user_id: str,
    *,
    settings: Settings | None = None,
) -> bool:
    """Apply kill switch, deny, explicit/internal allow, then stable cohort."""

    gate = capability_gate(capability, settings=settings)
    if not gate.enabled:
        return False
    if user_id in gate.denylist:
        return False
    if user_id in gate.allowlist or user_id in gate.internal_allowlist:
        return True
    return stable_user_cohort(user_id) < gate.rollout_percent
