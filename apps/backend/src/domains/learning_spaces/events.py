"""
Learning Spaces domain — Domain events.
"""

from src.shared.events import LearningSpaceEvents, emit


async def emit_space_created(user_id: str, space_id: str) -> None:
    await emit(LearningSpaceEvents.SPACE_CREATED, {"user_id": user_id, "space_id": space_id})


async def emit_member_joined(user_id: str, space_id: str, role: str = "LEARNER") -> None:
    await emit(
        LearningSpaceEvents.MEMBER_JOINED, {"user_id": user_id, "space_id": space_id, "role": role}
    )


async def emit_member_left(user_id: str, space_id: str) -> None:
    await emit(LearningSpaceEvents.MEMBER_LEFT, {"user_id": user_id, "space_id": space_id})


async def emit_role_changed(user_id: str, space_id: str, new_role: str) -> None:
    await emit(
        LearningSpaceEvents.ROLE_CHANGED,
        {"user_id": user_id, "space_id": space_id, "new_role": new_role},
    )
