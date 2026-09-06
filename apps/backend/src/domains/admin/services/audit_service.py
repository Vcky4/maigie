"""Audit trail for privileged actions.

This replaces a stub that accepted every call and discarded it. The stub's signature
was ``log_admin_action(action, admin_id="", **kwargs)`` while its only caller passes
``admin_user_id``, ``resource_type``, ``resource_id`` and ``details`` by keyword, so
every one of those arguments was absorbed by ``**kwargs`` and dropped. The practical
effect was that an administrator adjusting a user's purchased credit balance left no
record at all.

The signature here matches the caller and the pre-migration original.

Records are written to the ``AuditLog`` table, which already existed in the database but
had no SQLAlchemy model, and held zero rows. A structured application-log line is emitted
alongside the insert so the trail survives even if the write fails.

Failures never propagate: an audit write must not roll back the privileged operation it
describes. A failed insert is logged at error level, and the log line still carries the
full record, so the event is not lost outright.
"""

import logging
from datetime import UTC, datetime
from typing import Any

logger = logging.getLogger(__name__)


async def log_admin_action(
    admin_user_id: str,
    action: str,
    resource_type: str,
    resource_id: str | None = None,
    details: dict[str, Any] | None = None,
    **_kwargs: Any,
) -> None:
    """Record a privileged action taken by an administrator.

    Args:
        admin_user_id: The administrator performing the action.
        action: What was done, for example ``adjust_purchased_credits``.
        resource_type: The kind of thing affected, for example ``user``.
        resource_id: The specific thing affected, when there is one.
        details: Any additional context worth keeping, such as before and after values.
    """
    timestamp = datetime.now(UTC)

    # Nothing in this function may escape to the caller, so the two side effects are
    # guarded separately: a logging failure must not prevent the insert, and an insert
    # failure must not discard the log line.
    try:
        message = (
            f"ADMIN_ACTION: admin_user_id={admin_user_id}, action={action}, "
            f"resource_type={resource_type}, resource_id={resource_id}"
        )
        if details:
            message += f", details={details}"

        logger.info(
            message,
            extra={
                "audit": True,
                "admin_user_id": admin_user_id,
                "action": action,
                "resource_type": resource_type,
                "resource_id": resource_id,
                "details": details,
                "timestamp": timestamp.isoformat(),
            },
        )
    except Exception:
        pass

    try:
        from src.domains.admin.db_models import AuditLog
        from src.shared.database import get_session_factory

        factory = get_session_factory()
        async with factory() as session:
            session.add(
                AuditLog(
                    admin_user_id=admin_user_id,
                    action_type=action,
                    resource_type=resource_type,
                    resource_id=resource_id,
                    details=details,
                    # The column is a naive timestamp, so store UTC without the offset
                    # rather than letting the driver decide.
                    timestamp=timestamp.replace(tzinfo=None),
                )
            )
            await session.commit()
    except Exception:
        try:
            logger.error(
                "Failed to persist admin action %s by %s to AuditLog",
                action,
                admin_user_id,
                exc_info=True,
            )
        except Exception:
            pass
