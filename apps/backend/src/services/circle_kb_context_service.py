"""
Circle Course Context Service.

Retrieves courses linked to a chat group and formats their content
(modules, topics, resources) for injection into the AI prompt.
"""

import logging

from prisma import Prisma

logger = logging.getLogger(__name__)


async def get_knowledge_context_for_chat_group(
    db_client: Prisma, circle_id: str, chat_group_id: str
) -> str | None:
    """
    Fetch all courses linked to a chat group and format their content
    as context text for the AI prompt.

    Returns None if no courses are linked to this group.
    """
    links = await db_client.circlegroupcourselink.find_many(
        where={"circleId": circle_id, "chatGroupId": chat_group_id},
        include={
            "course": {
                "include": {
                    "modules": {
                        "include": {"topics": True},
                        "order_by": {"order": "asc"},
                    },
                    "resources": True,
                    "notes": True,
                }
            }
        },
    )

    if not links:
        return None

    context_parts: list[str] = []
    context_parts.append("=== COURSE MATERIALS (use as your teaching reference) ===")

    for link in links:
        course = link.course
        if not course:
            continue

        context_parts.append(f"\n--- Course: {course.title} ---")
        if course.description:
            context_parts.append(f"Description: {course.description}")
        context_parts.append(f"Difficulty: {course.difficulty}")

        for module in course.modules or []:
            context_parts.append(f"\n  Module: {module.title}")
            if module.description:
                context_parts.append(f"    {module.description}")

            for topic in module.topics or []:
                context_parts.append(f"    Topic: {topic.title}")
                if topic.content:
                    # Truncate long topic content
                    content = topic.content[:800]
                    if len(topic.content) > 800:
                        content += "..."
                    context_parts.append(f"      {content}")

        # Include linked resources
        if course.resources:
            context_parts.append("\n  Resources:")
            for res in course.resources[:10]:  # Cap at 10
                context_parts.append(f"    - {res.title} ({res.type}): {res.url}")
                if res.description:
                    context_parts.append(f"      {res.description[:200]}")

        # Include linked notes (summaries)
        if course.notes:
            context_parts.append("\n  Notes:")
            for note in course.notes[:5]:  # Cap at 5
                context_parts.append(f"    - {note.title}")
                if note.summary:
                    context_parts.append(f"      Summary: {note.summary[:300]}")

    context_parts.append("\n=== END COURSE MATERIALS ===")
    context_parts.append(
        "Use the above course materials as your primary reference when answering questions. "
        "Guide students through the modules and topics. If a question is outside the linked courses, "
        "let them know and still try to help based on your general knowledge."
    )

    return "\n".join(context_parts)


async def check_group_access(
    db_client: Prisma, chat_group_id: str, user_id: str, circle_id: str
) -> bool:
    """
    Check if a user has access to a chat group.
    - PUBLIC groups: all circle members have access
    - PRIVATE groups: only assigned members (CircleChatGroupMember) have access
    - OWNER/ADMIN always have access
    """
    group = await db_client.circlechatgroup.find_unique(where={"id": chat_group_id})
    if not group:
        return False

    member = await db_client.circlemember.find_unique(
        where={"circleId_userId": {"circleId": circle_id, "userId": user_id}}
    )
    if not member:
        return False

    # OWNER and ADMIN always have access
    if str(member.role) in ("OWNER", "ADMIN"):
        return True

    # PUBLIC groups: all circle members have access
    visibility = getattr(group, "visibility", "PUBLIC")
    if visibility == "PUBLIC":
        return True

    # PRIVATE groups: check group membership
    group_member = await db_client.circlechatgroupmember.find_unique(
        where={"chatGroupId_userId": {"chatGroupId": chat_group_id, "userId": user_id}}
    )
    return group_member is not None
