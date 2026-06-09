"""
Circle Knowledge Base Service.

Handles CRUD for curricula, materials, knowledge links, and progress tracking.
Enforces role-based access: OWNER/ADMIN can manage everything,
TUTOR can create/update, MEMBER can only view/consume.
"""

import logging

from prisma import Prisma

logger = logging.getLogger(__name__)


# --- Helpers ---


async def _get_member_role(db_client: Prisma, circle_id: str, user_id: str) -> str | None:
    """Get the user's role in the circle, or None if not a member."""
    member = await db_client.circlemember.find_unique(
        where={"circleId_userId": {"circleId": circle_id, "userId": user_id}}
    )
    return str(member.role) if member else None


def _can_manage(role: str | None) -> bool:
    """OWNER and ADMIN can manage (create, update, delete)."""
    return role in ("OWNER", "ADMIN")


def _can_create(role: str | None) -> bool:
    """OWNER, ADMIN, and TUTOR can create/update content."""
    return role in ("OWNER", "ADMIN", "TUTOR")


def _can_view(role: str | None) -> bool:
    """All circle members can view."""
    return role is not None


# --- Curriculum CRUD ---


async def list_curricula(db_client: Prisma, circle_id: str, user_id: str) -> list:
    """List curricula. Members only see PUBLISHED; admins see all."""
    role = await _get_member_role(db_client, circle_id, user_id)
    if not _can_view(role):
        raise PermissionError("Not a member of this circle.")

    where: dict = {"circleId": circle_id}
    if not _can_create(role):
        where["status"] = "PUBLISHED"

    curricula = await db_client.circlecurriculum.find_many(
        where=where,
        order={"order": "asc"},
        include={"sections": {"include": {"_count": True}}},
    )
    return curricula


async def get_curriculum(db_client: Prisma, circle_id: str, curriculum_id: str, user_id: str):
    """Get curriculum detail with sections and materials."""
    role = await _get_member_role(db_client, circle_id, user_id)
    if not _can_view(role):
        raise PermissionError("Not a member of this circle.")

    curriculum = await db_client.circlecurriculum.find_first(
        where={"id": curriculum_id, "circleId": circle_id},
        include={
            "sections": {
                "order_by": {"order": "asc"},
                "include": {
                    "materials": {
                        "include": {"material": True},
                        "order_by": {"order": "asc"},
                    }
                },
            }
        },
    )
    if not curriculum:
        return None

    # Members can't see DRAFT curricula
    if str(curriculum.status) != "PUBLISHED" and not _can_create(role):
        return None

    return curriculum


async def create_curriculum(db_client: Prisma, circle_id: str, user_id: str, data: dict):
    """Create a new curriculum. Requires OWNER, ADMIN, or TUTOR."""
    role = await _get_member_role(db_client, circle_id, user_id)
    if not _can_create(role):
        raise PermissionError("Insufficient permissions to create curriculum.")

    curriculum = await db_client.circlecurriculum.create(
        data={
            "circleId": circle_id,
            "createdById": user_id,
            "title": data["title"],
            "description": data.get("description"),
            "coverUrl": data.get("coverUrl"),
            "status": data.get("status", "DRAFT"),
        }
    )
    logger.info("Created curriculum %s in circle %s by user %s", curriculum.id, circle_id, user_id)
    return curriculum


async def update_curriculum(
    db_client: Prisma, circle_id: str, curriculum_id: str, user_id: str, data: dict
):
    """Update a curriculum. Requires OWNER, ADMIN, or TUTOR."""
    role = await _get_member_role(db_client, circle_id, user_id)
    if not _can_create(role):
        raise PermissionError("Insufficient permissions to update curriculum.")

    existing = await db_client.circlecurriculum.find_first(
        where={"id": curriculum_id, "circleId": circle_id}
    )
    if not existing:
        return None

    update_data = {}
    if data.get("title") is not None:
        update_data["title"] = data["title"]
    if data.get("description") is not None:
        update_data["description"] = data["description"]
    if data.get("coverUrl") is not None:
        update_data["coverUrl"] = data["coverUrl"]
    if data.get("status") is not None:
        update_data["status"] = data["status"]
    if data.get("order") is not None:
        update_data["order"] = data["order"]

    if not update_data:
        return existing

    curriculum = await db_client.circlecurriculum.update(
        where={"id": curriculum_id}, data=update_data
    )
    return curriculum


async def delete_curriculum(
    db_client: Prisma, circle_id: str, curriculum_id: str, user_id: str
) -> bool:
    """Delete a curriculum. Requires OWNER or ADMIN."""
    role = await _get_member_role(db_client, circle_id, user_id)
    if not _can_manage(role):
        raise PermissionError("Only owner or admin can delete curricula.")

    existing = await db_client.circlecurriculum.find_first(
        where={"id": curriculum_id, "circleId": circle_id}
    )
    if not existing:
        return False

    await db_client.circlecurriculum.delete(where={"id": curriculum_id})
    logger.info(
        "Deleted curriculum %s from circle %s by user %s", curriculum_id, circle_id, user_id
    )
    return True


# --- Section CRUD ---


async def create_section(
    db_client: Prisma, circle_id: str, curriculum_id: str, user_id: str, data: dict
):
    """Create a section in a curriculum. Requires OWNER, ADMIN, or TUTOR."""
    role = await _get_member_role(db_client, circle_id, user_id)
    if not _can_create(role):
        raise PermissionError("Insufficient permissions to create section.")

    curriculum = await db_client.circlecurriculum.find_first(
        where={"id": curriculum_id, "circleId": circle_id}
    )
    if not curriculum:
        raise ValueError("Curriculum not found.")

    # Get next order
    last_section = await db_client.circlecurriculumsection.find_first(
        where={"curriculumId": curriculum_id},
        order={"order": "desc"},
    )
    next_order = (last_section.order + 1) if last_section else 0

    section = await db_client.circlecurriculumsection.create(
        data={
            "curriculumId": curriculum_id,
            "title": data["title"],
            "description": data.get("description"),
            "objectives": data.get("objectives"),
            "estimatedMinutes": data.get("estimatedMinutes", 30),
            "order": next_order,
        }
    )

    # Link materials if provided
    material_ids = data.get("materialIds") or []
    for i, material_id in enumerate(material_ids):
        await db_client.circlesectionmaterial.create(
            data={
                "sectionId": section.id,
                "materialId": material_id,
                "order": float(i),
            }
        )

    # Recalculate progress for members who started this curriculum
    await _recalculate_curriculum_progress(db_client, curriculum_id)

    return section


async def update_section(
    db_client: Prisma, circle_id: str, curriculum_id: str, section_id: str, user_id: str, data: dict
):
    """Update a section. Requires OWNER, ADMIN, or TUTOR."""
    role = await _get_member_role(db_client, circle_id, user_id)
    if not _can_create(role):
        raise PermissionError("Insufficient permissions to update section.")

    existing = await db_client.circlecurriculumsection.find_first(
        where={"id": section_id, "curriculumId": curriculum_id}
    )
    if not existing:
        return None

    # Verify curriculum belongs to circle
    curriculum = await db_client.circlecurriculum.find_first(
        where={"id": curriculum_id, "circleId": circle_id}
    )
    if not curriculum:
        return None

    update_data = {}
    if data.get("title") is not None:
        update_data["title"] = data["title"]
    if data.get("description") is not None:
        update_data["description"] = data["description"]
    if data.get("objectives") is not None:
        update_data["objectives"] = data["objectives"]
    if data.get("estimatedMinutes") is not None:
        update_data["estimatedMinutes"] = data["estimatedMinutes"]
    if data.get("order") is not None:
        update_data["order"] = data["order"]

    if not update_data:
        return existing

    return await db_client.circlecurriculumsection.update(
        where={"id": section_id}, data=update_data
    )


async def delete_section(
    db_client: Prisma, circle_id: str, curriculum_id: str, section_id: str, user_id: str
) -> bool:
    """Delete a section. Requires OWNER or ADMIN."""
    role = await _get_member_role(db_client, circle_id, user_id)
    if not _can_manage(role):
        raise PermissionError("Only owner or admin can delete sections.")

    # Verify ownership chain
    curriculum = await db_client.circlecurriculum.find_first(
        where={"id": curriculum_id, "circleId": circle_id}
    )
    if not curriculum:
        return False

    existing = await db_client.circlecurriculumsection.find_first(
        where={"id": section_id, "curriculumId": curriculum_id}
    )
    if not existing:
        return False

    await db_client.circlecurriculumsection.delete(where={"id": section_id})
    await _recalculate_curriculum_progress(db_client, curriculum_id)
    return True


# --- Material CRUD ---


async def list_materials(
    db_client: Prisma,
    circle_id: str,
    user_id: str,
    folder: str | None = None,
    material_type: str | None = None,
) -> list:
    """List materials in a circle's knowledge base."""
    role = await _get_member_role(db_client, circle_id, user_id)
    if not _can_view(role):
        raise PermissionError("Not a member of this circle.")

    where: dict = {"circleId": circle_id}
    if folder is not None:
        where["folder"] = folder
    if material_type is not None:
        where["type"] = material_type

    return await db_client.circlematerial.find_many(where=where, order={"createdAt": "desc"})


async def get_material(db_client: Prisma, circle_id: str, material_id: str, user_id: str):
    """Get a single material and increment access count."""
    role = await _get_member_role(db_client, circle_id, user_id)
    if not _can_view(role):
        raise PermissionError("Not a member of this circle.")

    material = await db_client.circlematerial.find_first(
        where={"id": material_id, "circleId": circle_id}
    )
    if not material:
        return None

    # Increment access count
    await db_client.circlematerial.update(
        where={"id": material_id}, data={"accessCount": {"increment": 1}}
    )
    return material


async def create_material(db_client: Prisma, circle_id: str, user_id: str, data: dict):
    """Create a material. Requires OWNER, ADMIN, or TUTOR."""
    role = await _get_member_role(db_client, circle_id, user_id)
    if not _can_create(role):
        raise PermissionError("Insufficient permissions to upload material.")

    material = await db_client.circlematerial.create(
        data={
            "circleId": circle_id,
            "uploadedById": user_id,
            "title": data["title"],
            "description": data.get("description"),
            "type": data["type"],
            "fileUrl": data.get("fileUrl"),
            "fileSize": data.get("fileSize"),
            "mimeType": data.get("mimeType"),
            "externalUrl": data.get("externalUrl"),
            "folder": data.get("folder"),
        }
    )
    logger.info("Created material %s in circle %s by user %s", material.id, circle_id, user_id)
    return material


async def update_material(
    db_client: Prisma, circle_id: str, material_id: str, user_id: str, data: dict
):
    """Update material metadata. Requires OWNER, ADMIN, or TUTOR."""
    role = await _get_member_role(db_client, circle_id, user_id)
    if not _can_create(role):
        raise PermissionError("Insufficient permissions to update material.")

    existing = await db_client.circlematerial.find_first(
        where={"id": material_id, "circleId": circle_id}
    )
    if not existing:
        return None

    update_data = {}
    if data.get("title") is not None:
        update_data["title"] = data["title"]
    if data.get("description") is not None:
        update_data["description"] = data["description"]
    if data.get("folder") is not None:
        update_data["folder"] = data["folder"]

    if not update_data:
        return existing

    return await db_client.circlematerial.update(where={"id": material_id}, data=update_data)


async def delete_material(
    db_client: Prisma, circle_id: str, material_id: str, user_id: str
) -> bool:
    """Delete a material. Requires OWNER or ADMIN."""
    role = await _get_member_role(db_client, circle_id, user_id)
    if not _can_manage(role):
        raise PermissionError("Only owner or admin can delete materials.")

    existing = await db_client.circlematerial.find_first(
        where={"id": material_id, "circleId": circle_id}
    )
    if not existing:
        return False

    await db_client.circlematerial.delete(where={"id": material_id})
    logger.info("Deleted material %s from circle %s by user %s", material_id, circle_id, user_id)
    return True


async def list_folders(db_client: Prisma, circle_id: str, user_id: str) -> list[str]:
    """List distinct folder names for a circle's materials."""
    role = await _get_member_role(db_client, circle_id, user_id)
    if not _can_view(role):
        raise PermissionError("Not a member of this circle.")

    materials = await db_client.circlematerial.find_many(
        where={"circleId": circle_id, "folder": {"not": None}},
        distinct=["folder"],
    )
    return [m.folder for m in materials if m.folder]


# --- Knowledge Links ---


async def create_knowledge_link(db_client: Prisma, circle_id: str, user_id: str, data: dict):
    """Create a knowledge link. Requires OWNER or ADMIN."""
    role = await _get_member_role(db_client, circle_id, user_id)
    if not _can_manage(role):
        raise PermissionError("Only owner or admin can manage knowledge links.")

    # Validate source: at least one of curriculum, section, material
    source_count = sum(1 for k in ("curriculumId", "sectionId", "materialId") if data.get(k))
    if source_count != 1:
        raise ValueError("Exactly one source (curriculumId, sectionId, or materialId) is required.")

    # Validate target: at least one of chatGroup or session
    target_count = sum(1 for k in ("chatGroupId", "sessionId") if data.get(k))
    if target_count != 1:
        raise ValueError("Exactly one target (chatGroupId or sessionId) is required.")

    link = await db_client.circleknowledgelink.create(
        data={
            "circleId": circle_id,
            "createdById": user_id,
            "curriculumId": data.get("curriculumId"),
            "sectionId": data.get("sectionId"),
            "materialId": data.get("materialId"),
            "chatGroupId": data.get("chatGroupId"),
            "sessionId": data.get("sessionId"),
        }
    )
    logger.info("Created knowledge link %s in circle %s", link.id, circle_id)
    return link


async def delete_knowledge_link(
    db_client: Prisma, circle_id: str, link_id: str, user_id: str
) -> bool:
    """Delete a knowledge link. Requires OWNER or ADMIN."""
    role = await _get_member_role(db_client, circle_id, user_id)
    if not _can_manage(role):
        raise PermissionError("Only owner or admin can manage knowledge links.")

    existing = await db_client.circleknowledgelink.find_first(
        where={"id": link_id, "circleId": circle_id}
    )
    if not existing:
        return False

    await db_client.circleknowledgelink.delete(where={"id": link_id})
    return True


async def list_links_for_chat_group(
    db_client: Prisma, circle_id: str, chat_group_id: str, user_id: str
) -> list:
    """List knowledge links for a chat group."""
    role = await _get_member_role(db_client, circle_id, user_id)
    if not _can_view(role):
        raise PermissionError("Not a member of this circle.")

    return await db_client.circleknowledgelink.find_many(
        where={"circleId": circle_id, "chatGroupId": chat_group_id},
        include={"curriculum": True, "section": True, "material": True},
    )


async def list_links_for_session(
    db_client: Prisma, circle_id: str, session_id: str, user_id: str
) -> list:
    """List knowledge links for a session."""
    role = await _get_member_role(db_client, circle_id, user_id)
    if not _can_view(role):
        raise PermissionError("Not a member of this circle.")

    return await db_client.circleknowledgelink.find_many(
        where={"circleId": circle_id, "sessionId": session_id},
        include={"curriculum": True, "section": True, "material": True},
    )


# --- Progress Tracking ---


async def mark_section_complete(
    db_client: Prisma, circle_id: str, curriculum_id: str, section_id: str, user_id: str
):
    """Mark a section as complete for the current user."""
    role = await _get_member_role(db_client, circle_id, user_id)
    if not _can_view(role):
        raise PermissionError("Not a member of this circle.")

    # Verify section belongs to curriculum in this circle
    section = await db_client.circlecurriculumsection.find_first(
        where={"id": section_id, "curriculumId": curriculum_id}
    )
    if not section:
        raise ValueError("Section not found.")

    curriculum = await db_client.circlecurriculum.find_first(
        where={"id": curriculum_id, "circleId": circle_id}
    )
    if not curriculum:
        raise ValueError("Curriculum not found.")

    # Upsert section progress
    from datetime import datetime, timezone

    await db_client.circlesectionprogress.upsert(
        where={"sectionId_userId": {"sectionId": section_id, "userId": user_id}},
        create={
            "sectionId": section_id,
            "userId": user_id,
            "completed": True,
            "completedAt": datetime.now(timezone.utc),
        },
        update={
            "completed": True,
            "completedAt": datetime.now(timezone.utc),
        },
    )

    # Update curriculum progress
    await _update_user_curriculum_progress(db_client, curriculum_id, user_id)

    return {"sectionId": section_id, "completed": True}


async def get_my_curriculum_progress(
    db_client: Prisma, circle_id: str, curriculum_id: str, user_id: str
):
    """Get the current user's progress for a curriculum."""
    role = await _get_member_role(db_client, circle_id, user_id)
    if not _can_view(role):
        raise PermissionError("Not a member of this circle.")

    progress = await db_client.circlecurriculumprogress.find_unique(
        where={"curriculumId_userId": {"curriculumId": curriculum_id, "userId": user_id}}
    )
    if not progress:
        # Not started yet
        total = await db_client.circlecurriculumsection.count(where={"curriculumId": curriculum_id})
        return {
            "curriculumId": curriculum_id,
            "userId": user_id,
            "completedSections": 0,
            "totalSections": total,
            "percentage": 0.0,
            "completedAt": None,
            "startedAt": None,
        }
    return progress


async def get_all_members_progress(
    db_client: Prisma, circle_id: str, curriculum_id: str, user_id: str
) -> list:
    """Get all members' progress for a curriculum. Requires OWNER or ADMIN."""
    role = await _get_member_role(db_client, circle_id, user_id)
    if not _can_manage(role):
        raise PermissionError("Only owner or admin can view all members' progress.")

    return await db_client.circlecurriculumprogress.find_many(
        where={"curriculumId": curriculum_id},
        include={"user": True},
    )


# --- Search ---


async def search_knowledge_base(
    db_client: Prisma, circle_id: str, user_id: str, query: str
) -> dict:
    """Search across curricula and materials in the circle's knowledge base."""
    role = await _get_member_role(db_client, circle_id, user_id)
    if not _can_view(role):
        raise PermissionError("Not a member of this circle.")

    # Search curricula
    curricula_where: dict = {
        "circleId": circle_id,
        "OR": [
            {"title": {"contains": query, "mode": "insensitive"}},
            {"description": {"contains": query, "mode": "insensitive"}},
        ],
    }
    if not _can_create(role):
        curricula_where["status"] = "PUBLISHED"

    curricula = await db_client.circlecurriculum.find_many(where=curricula_where, take=20)

    # Search materials
    materials = await db_client.circlematerial.find_many(
        where={
            "circleId": circle_id,
            "OR": [
                {"title": {"contains": query, "mode": "insensitive"}},
                {"description": {"contains": query, "mode": "insensitive"}},
            ],
        },
        take=20,
    )

    return {"curricula": curricula, "materials": materials}


# --- Internal Helpers ---


async def _update_user_curriculum_progress(db_client: Prisma, curriculum_id: str, user_id: str):
    """Recalculate and upsert a user's curriculum progress."""
    total_sections = await db_client.circlecurriculumsection.count(
        where={"curriculumId": curriculum_id}
    )
    if total_sections == 0:
        return

    # Get section IDs for this curriculum
    sections = await db_client.circlecurriculumsection.find_many(
        where={"curriculumId": curriculum_id}, select={"id": True}
    )
    section_ids = [s.id for s in sections]

    completed_count = await db_client.circlesectionprogress.count(
        where={
            "sectionId": {"in": section_ids},
            "userId": user_id,
            "completed": True,
        }
    )

    percentage = (completed_count / total_sections) * 100.0
    from datetime import datetime, timezone

    completed_at = datetime.now(timezone.utc) if completed_count >= total_sections else None

    await db_client.circlecurriculumprogress.upsert(
        where={"curriculumId_userId": {"curriculumId": curriculum_id, "userId": user_id}},
        create={
            "curriculumId": curriculum_id,
            "userId": user_id,
            "completedSections": completed_count,
            "totalSections": total_sections,
            "percentage": percentage,
            "completedAt": completed_at,
        },
        update={
            "completedSections": completed_count,
            "totalSections": total_sections,
            "percentage": percentage,
            "completedAt": completed_at,
        },
    )


async def _recalculate_curriculum_progress(db_client: Prisma, curriculum_id: str):
    """Recalculate progress for all members who have started this curriculum."""
    all_progress = await db_client.circlecurriculumprogress.find_many(
        where={"curriculumId": curriculum_id}
    )
    for progress in all_progress:
        await _update_user_curriculum_progress(db_client, curriculum_id, progress.userId)
