"""Privacy boundary for notification decision and provider audit records.

Writers must validate through these contracts before persisting JSON metadata.
Raw prompts, learning content, provider payloads, credentials, and tokens do not
belong in the notification ledger.
"""

from __future__ import annotations

import re
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field, field_validator

from .taxonomy import NotificationChannel

_AUDIT_KEY = re.compile(r"^[a-z][a-z0-9_.]{0,63}$")
_SECRET_PATTERNS = (
    re.compile(r"(?i)(bearer\s+)[^\s,;]+"),
    re.compile(r"(?i)((?:api[_-]?key|token|secret|password)\s*[=:]\s*)[^\s,;]+"),
)


class _AuditModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class DecisionAuditPayload(_AuditModel):
    """Allowlisted, compact features and outputs for a decision record."""

    features: dict[str, str | int | float | bool | None] = Field(max_length=64)
    candidate_ids: list[str] = Field(default_factory=list, max_length=50)
    selected_candidate_id: str | None = Field(default=None, max_length=128)
    selected_channels: list[NotificationChannel] = Field(default_factory=list, max_length=4)
    selected_eligible_at: datetime | None = None
    selected_group_key: str | None = Field(default=None, max_length=128)
    reason_codes: list[str] = Field(default_factory=list, max_length=20)

    @field_validator("features")
    @classmethod
    def validate_features(
        cls, value: dict[str, str | int | float | bool | None]
    ) -> dict[str, str | int | float | bool | None]:
        for key, feature in value.items():
            if not _AUDIT_KEY.fullmatch(key):
                raise ValueError(f"Invalid audit feature key: {key}")
            if isinstance(feature, str) and len(feature) > 256:
                raise ValueError(f"Audit feature value is too long: {key}")
        return value

    @field_validator("candidate_ids", "reason_codes")
    @classmethod
    def validate_short_values(cls, value: list[str]) -> list[str]:
        if any(not item or len(item) > 128 for item in value):
            raise ValueError("Audit identifiers and reason codes must be 1-128 characters")
        return value


class ProviderAttemptAuditPayload(_AuditModel):
    """Normalized provider result; raw responses and request data are forbidden."""

    http_status: int | None = Field(default=None, ge=100, le=599)
    provider_code: str | None = Field(default=None, max_length=128)
    receipt_status: str | None = Field(default=None, max_length=64)
    retry_after_seconds: int | None = Field(default=None, ge=0, le=86_400)
    destination_disabled: bool = False


def redact_error_detail(detail: str | None, *, limit: int = 1000) -> str | None:
    """Redact common credentials and bound diagnostic text before persistence."""

    if detail is None:
        return None
    redacted = detail
    for pattern in _SECRET_PATTERNS:
        redacted = pattern.sub(r"\1[REDACTED]", redacted)
    return redacted[:limit]
