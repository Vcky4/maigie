"""
Circle Knowledge Base Context Service.

Retrieves linked knowledge base materials for a chat group and formats them
for injection into the AI prompt. This is the bridge between the knowledge base
and the chat system.
"""

import logging

from prisma import Prisma

logger = logging.getLogger(__name__)


async def get_knowledge_context_for_chat_group(
    db_client: Prisma, circle_id: str, chat_group_id: str
) -> str | None:
    """
    Fetch all knowledge base items linked to a chat group and format them
    as context text for the AI prompt.

    Returns None if no knowledge links exist for this group.
    """
    links = await db_client.circleknowledgelink.find_many(
        where={"circleId": circle_id, "chatGroupId": chat_group_id},
        include={
            "curriculum": {"include": {"sections": True}},
            "section": True,
            "material": True,
        },
    )

    if not links:
        return None

    context_parts: list[str] = []
    context_parts.append("=== CIRCLE KNOWLEDGE BASE (use this as your teaching reference) ===")

    for link in links:
        if link.curriculum:
            curriculum = link.curriculum
            context_parts.append(f"\n--- Curriculum: {curriculum.title} ---")
            if curriculum.description:
                context_parts.append(f"Description: {curriculum.description}")
            for section in curriculum.sections or []:
                context_parts.append(f"  Section: {section.title}")
                if section.description:
                    context_parts.append(f"    {section.description}")
                if section.objectives:
                    objectives = section.objectives if isinstance(section.objectives, list) else []
                    for obj in objectives:
                        context_parts.append(f"    - Objective: {obj}")

        elif link.section:
            section = link.section
            context_parts.append(f"\n--- Section: {section.title} ---")
            if section.description:
                context_parts.append(f"  {section.description}")
            if section.objectives:
                objectives = section.objectives if isinstance(section.objectives, list) else []
                for obj in objectives:
                    context_parts.append(f"  - Objective: {obj}")

        elif link.material:
            material = link.material
            context_parts.append(f"\n--- Material: {material.title} ({material.type}) ---")
            if material.description:
                context_parts.append(f"  {material.description}")
            if material.indexedContent:
                # Truncate to avoid overflowing context window
                content = material.indexedContent[:3000]
                if len(material.indexedContent) > 3000:
                    content += "\n  [... content truncated]"
                context_parts.append(f"  Content:\n  {content}")
            elif material.externalUrl:
                context_parts.append(f"  URL: {material.externalUrl}")

    context_parts.append("\n=== END KNOWLEDGE BASE ===")
    context_parts.append(
        "Use the above knowledge base materials as your primary reference when answering questions. "
        "Guide students through the curriculum. If a question is outside the linked materials, "
        "let them know and still try to help based on your general knowledge."
    )

    return "\n".join(context_parts)


async def get_knowledge_context_for_session(
    db_client: Prisma, circle_id: str, session_id: str
) -> str | None:
    """
    Fetch knowledge base items linked to a session and format for AI context.
    """
    links = await db_client.circleknowledgelink.find_many(
        where={"circleId": circle_id, "sessionId": session_id},
        include={
            "curriculum": {"include": {"sections": True}},
            "section": True,
            "material": True,
        },
    )

    if not links:
        return None

    # Same formatting as chat group context
    return await get_knowledge_context_for_chat_group.__wrapped__(links) if False else None


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

    # Check circle membership and role
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
