"""Recording that a transactional message was attempted, without changing whether it is sent.

Two rules shape this module.

**Evidence must never break a send.** These messages are mandatory — a password reset that fails
because the audit write failed is a worse outcome than a reset with no audit row. So every
function here swallows its own errors and logs; the caller's behaviour is unchanged whether the
recording works or not.

**Evidence must never become a copy of the message.** No code, no body, no subject, no address —
only the address's hash, the class of message, a purpose label, and what the provider said. A
security audit trail that quietly accumulates reset codes is a liability, not an asset.

The model lives in the notifications domain because that domain owns communications. It is
imported inside the functions rather than at module scope: this is shared infrastructure, and a
module-level import of a domain would invert the dependency for every importer of the email
module.
"""

from __future__ import annotations

import hashlib
import logging
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Literal

logger = logging.getLogger(__name__)

MessageClass = Literal["AUTH", "SECURITY", "BILLING", "MEMBERSHIP", "OPERATIONS"]
MessageStatus = Literal["ACCEPTED", "FAILED", "SKIPPED"]


@dataclass(frozen=True)
class TransactionalEvidence:
    """What is known about a transactional send before it is attempted."""

    message_class: MessageClass
    purpose: str
    user_id: str | None = None


def address_hash(email: str) -> str:
    """Match `notifications.email_delivery.address_reference` so hashes are comparable."""

    return hashlib.sha256(email.strip().lower().encode("utf-8")).hexdigest()


async def record_transactional_message(
    *,
    evidence: TransactionalEvidence,
    to_email: str,
    status: MessageStatus,
    provider: str | None = None,
    provider_message_id: str | None = None,
    error_code: str | None = None,
    error_detail: str | None = None,
    requested_at: datetime | None = None,
    duration_ms: int | None = None,
) -> None:
    """Append one evidence row. Never raises."""

    try:
        # `User` is imported for its side effect, not its name: `OutboundMessage.userId` is a
        # foreign key to `User.id`, and SQLAlchemy can only resolve that if both mappers are
        # registered on the shared metadata. Importing the notifications models alone raises
        # `NoReferencedTableError` at flush time — which is exactly what happens on a
        # worker-invoked path, where nothing else has pulled the identity models in yet.
        from src.domains.identity.db_models import User  # noqa: F401
        from src.domains.notifications.db_models import OutboundMessage
        from src.shared.database import get_session_factory

        factory = get_session_factory()
        async with factory() as session, session.begin():
            session.add(
                OutboundMessage(
                    message_class=evidence.message_class,
                    purpose=evidence.purpose[:64],
                    address_hash=address_hash(to_email),
                    user_id=evidence.user_id,
                    provider=provider,
                    provider_message_id=provider_message_id,
                    status=status,
                    error_code=(error_code or "")[:64] or None,
                    error_detail=(error_detail or "")[:500] or None,
                    requested_at=requested_at or datetime.now(UTC),
                    duration_ms=duration_ms,
                )
            )
    except Exception:
        # A mandatory message must not fail because its audit row could not be written.
        logger.warning(
            "Could not record outbound message evidence",
            exc_info=True,
            extra={"purpose": evidence.purpose, "status": status},
        )
