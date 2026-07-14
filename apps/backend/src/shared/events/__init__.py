"""Domain event bus — cross-domain communication without coupling."""

from .bus import clear_handlers, emit, get_handler_count, listen
from .types import (
    BillingEvents,
    ClassroomEvents,
    IdentityEvents,
    IntelligenceEvents,
    KnowledgeEvents,
    LearningSpaceEvents,
    ProgressEvents,
)

__all__ = [
    "emit",
    "listen",
    "clear_handlers",
    "get_handler_count",
    "IdentityEvents",
    "KnowledgeEvents",
    "LearningSpaceEvents",
    "ClassroomEvents",
    "IntelligenceEvents",
    "ProgressEvents",
    "BillingEvents",
]
