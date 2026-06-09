"""
Circle Knowledge Base routes.

Manages curricula, materials, knowledge links (to chat groups/sessions),
and per-member progress tracking. Mounted at /api/v1/circles/{circle_id}/knowledge-base.

Access control:
- OWNER, ADMIN: full manage (create, update, delete, link)
- TUTOR: create/update curricula and materials
- MEMBER: view content, track own progress
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, File, HTTPException, Query, UploadFile, status

from src.dependencies import CurrentUser, db
from src.models.circle_knowledge_base import (
    CurriculumCreate,
    CurriculumSectionCreate,
    CurriculumSectionUpdate,
    CurriculumUpdate,
    KnowledgeLinkCreate,
    MaterialCreate,
    MaterialUpdate,
)
from src.services import circle_knowledge_base_service as kb_service

router = APIRouter(
    prefix="/api/v1/circles/{circle_id}/knowledge-base",
    tags=["circle-knowledge-base"],
)
logger = logging.getLogger(__name__)


# ==========================================
#  CURRICULA
# ==========================================


@router.get("/curricula")
async def list_curricula(circle_id: str, current_user: CurrentUser):
    """List curricula in the circle's knowledge base."""
    try:
        curricula = await kb_service.list_curricula(db, circle_id, current_user.id)
        return {
            "curricula": [
                {
                    "id": c.id,
                    "circleId": c.circleId,
                    "title": c.title,
                    "description": c.description,
                    "coverUrl": c.coverUrl,
                    "status": str(c.status),
                    "order": c.order,
                    "createdById": c.createdById,
                    "sectionCount": len(c.sections) if hasattr(c, "sections") else 0,
                    "createdAt": c.createdAt.isoformat(),
                    "updatedAt": c.updatedAt.isoformat(),
                }
                for c in curricula
            ]
        }
    except PermissionError as e:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(e))
    except Exception as e:
        logger.error("Error listing curricula for circle %s: %s", circle_id, e)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to fetch curricula. The knowledge base may not be set up yet.",
        )


@router.post("/curricula", status_code=status.HTTP_201_CREATED)
async def create_curriculum(circle_id: str, body: CurriculumCreate, current_user: CurrentUser):
    """Create a new curriculum."""
    try:
        curriculum = await kb_service.create_curriculum(
            db, circle_id, current_user.id, body.model_dump()
        )
        return {
            "id": curriculum.id,
            "circleId": curriculum.circleId,
            "title": curriculum.title,
            "description": curriculum.description,
            "coverUrl": curriculum.coverUrl,
            "status": str(curriculum.status),
            "createdById": curriculum.createdById,
            "createdAt": curriculum.createdAt.isoformat(),
            "updatedAt": curriculum.updatedAt.isoformat(),
        }
    except PermissionError as e:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(e))


@router.post("/curricula/generate", status_code=status.HTTP_201_CREATED)
async def generate_curriculum_from_document(
    circle_id: str,
    current_user: CurrentUser,
    file: UploadFile = File(...),
    title: str = Query(None, description="Optional title override"),
):
    """Upload a document and use AI to generate a curriculum outline from its content.

    Supports PDF, DOCX, and text files. The AI will extract structure and create
    a curriculum with sections based on the document content.
    Consumes circle credits.
    """
    try:
        result = await kb_service.generate_curriculum_from_document(
            db, circle_id, current_user.id, file, title_override=title
        )
        return result
    except PermissionError as e:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(e))
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))
    except Exception as e:
        logger.error("Error generating curriculum from document: %s", e)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to generate curriculum from document.",
        )


@router.get("/curricula/{curriculum_id}")
async def get_curriculum(circle_id: str, curriculum_id: str, current_user: CurrentUser):
    """Get curriculum detail with sections and materials."""
    try:
        curriculum = await kb_service.get_curriculum(db, circle_id, curriculum_id, current_user.id)
        if not curriculum:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, detail="Curriculum not found."
            )

        sections = []
        for s in curriculum.sections:
            materials = []
            if hasattr(s, "materials") and s.materials:
                for sm in s.materials:
                    m = sm.material
                    materials.append(
                        {
                            "id": m.id,
                            "title": m.title,
                            "description": m.description,
                            "type": str(m.type),
                            "fileUrl": m.fileUrl,
                            "externalUrl": m.externalUrl,
                            "createdAt": m.createdAt.isoformat(),
                        }
                    )
            sections.append(
                {
                    "id": s.id,
                    "title": s.title,
                    "description": s.description,
                    "objectives": s.objectives,
                    "estimatedMinutes": s.estimatedMinutes,
                    "order": s.order,
                    "materials": materials,
                    "createdAt": s.createdAt.isoformat(),
                    "updatedAt": s.updatedAt.isoformat(),
                }
            )

        return {
            "id": curriculum.id,
            "circleId": curriculum.circleId,
            "title": curriculum.title,
            "description": curriculum.description,
            "coverUrl": curriculum.coverUrl,
            "status": str(curriculum.status),
            "order": curriculum.order,
            "createdById": curriculum.createdById,
            "sections": sections,
            "createdAt": curriculum.createdAt.isoformat(),
            "updatedAt": curriculum.updatedAt.isoformat(),
        }
    except PermissionError as e:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(e))


@router.put("/curricula/{curriculum_id}")
async def update_curriculum(
    circle_id: str, curriculum_id: str, body: CurriculumUpdate, current_user: CurrentUser
):
    """Update a curriculum."""
    try:
        curriculum = await kb_service.update_curriculum(
            db, circle_id, curriculum_id, current_user.id, body.model_dump(exclude_none=True)
        )
        if not curriculum:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, detail="Curriculum not found."
            )
        return {
            "id": curriculum.id,
            "title": curriculum.title,
            "description": curriculum.description,
            "status": str(curriculum.status),
            "updatedAt": curriculum.updatedAt.isoformat(),
        }
    except PermissionError as e:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(e))


@router.delete("/curricula/{curriculum_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_curriculum(circle_id: str, curriculum_id: str, current_user: CurrentUser):
    """Delete a curriculum."""
    try:
        deleted = await kb_service.delete_curriculum(db, circle_id, curriculum_id, current_user.id)
        if not deleted:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, detail="Curriculum not found."
            )
    except PermissionError as e:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(e))


# ==========================================
#  SECTIONS
# ==========================================


@router.post("/curricula/{curriculum_id}/sections", status_code=status.HTTP_201_CREATED)
async def create_section(
    circle_id: str, curriculum_id: str, body: CurriculumSectionCreate, current_user: CurrentUser
):
    """Add a section to a curriculum."""
    try:
        section = await kb_service.create_section(
            db, circle_id, curriculum_id, current_user.id, body.model_dump()
        )
        return {
            "id": section.id,
            "curriculumId": section.curriculumId,
            "title": section.title,
            "description": section.description,
            "objectives": section.objectives,
            "estimatedMinutes": section.estimatedMinutes,
            "order": section.order,
            "createdAt": section.createdAt.isoformat(),
        }
    except PermissionError as e:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(e))
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(e))


@router.put("/curricula/{curriculum_id}/sections/{section_id}")
async def update_section(
    circle_id: str,
    curriculum_id: str,
    section_id: str,
    body: CurriculumSectionUpdate,
    current_user: CurrentUser,
):
    """Update a section."""
    try:
        section = await kb_service.update_section(
            db,
            circle_id,
            curriculum_id,
            section_id,
            current_user.id,
            body.model_dump(exclude_none=True),
        )
        if not section:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Section not found.")
        return {
            "id": section.id,
            "title": section.title,
            "description": section.description,
            "objectives": section.objectives,
            "estimatedMinutes": section.estimatedMinutes,
            "order": section.order,
            "updatedAt": section.updatedAt.isoformat(),
        }
    except PermissionError as e:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(e))


@router.delete(
    "/curricula/{curriculum_id}/sections/{section_id}",
    status_code=status.HTTP_204_NO_CONTENT,
)
async def delete_section(
    circle_id: str, curriculum_id: str, section_id: str, current_user: CurrentUser
):
    """Delete a section."""
    try:
        deleted = await kb_service.delete_section(
            db, circle_id, curriculum_id, section_id, current_user.id
        )
        if not deleted:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Section not found.")
    except PermissionError as e:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(e))


# ==========================================
#  MATERIALS
# ==========================================


@router.get("/materials")
async def list_materials(
    circle_id: str,
    current_user: CurrentUser,
    folder: str | None = Query(None),
    type: str | None = Query(None, alias="type"),
):
    """List materials in the knowledge base."""
    try:
        materials = await kb_service.list_materials(
            db, circle_id, current_user.id, folder=folder, material_type=type
        )
        return {
            "materials": [
                {
                    "id": m.id,
                    "circleId": m.circleId,
                    "title": m.title,
                    "description": m.description,
                    "type": str(m.type),
                    "fileUrl": m.fileUrl,
                    "fileSize": m.fileSize,
                    "mimeType": m.mimeType,
                    "externalUrl": m.externalUrl,
                    "isIndexed": m.isIndexed,
                    "folder": m.folder,
                    "accessCount": m.accessCount,
                    "uploadedById": m.uploadedById,
                    "createdAt": m.createdAt.isoformat(),
                    "updatedAt": m.updatedAt.isoformat(),
                }
                for m in materials
            ]
        }
    except PermissionError as e:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(e))


@router.post("/materials", status_code=status.HTTP_201_CREATED)
async def create_material(circle_id: str, body: MaterialCreate, current_user: CurrentUser):
    """Upload/create a material."""
    try:
        material = await kb_service.create_material(
            db, circle_id, current_user.id, body.model_dump()
        )
        return {
            "id": material.id,
            "circleId": material.circleId,
            "title": material.title,
            "description": material.description,
            "type": str(material.type),
            "fileUrl": material.fileUrl,
            "externalUrl": material.externalUrl,
            "folder": material.folder,
            "createdAt": material.createdAt.isoformat(),
        }
    except PermissionError as e:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(e))


@router.get("/materials/folders")
async def list_folders(circle_id: str, current_user: CurrentUser):
    """List folder names used by materials."""
    try:
        folders = await kb_service.list_folders(db, circle_id, current_user.id)
        return {"folders": folders}
    except PermissionError as e:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(e))


@router.get("/materials/{material_id}")
async def get_material(circle_id: str, material_id: str, current_user: CurrentUser):
    """Get a single material."""
    try:
        material = await kb_service.get_material(db, circle_id, material_id, current_user.id)
        if not material:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Material not found.")
        return {
            "id": material.id,
            "circleId": material.circleId,
            "title": material.title,
            "description": material.description,
            "type": str(material.type),
            "fileUrl": material.fileUrl,
            "fileSize": material.fileSize,
            "mimeType": material.mimeType,
            "externalUrl": material.externalUrl,
            "isIndexed": material.isIndexed,
            "folder": material.folder,
            "accessCount": material.accessCount,
            "uploadedById": material.uploadedById,
            "createdAt": material.createdAt.isoformat(),
            "updatedAt": material.updatedAt.isoformat(),
        }
    except PermissionError as e:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(e))


@router.put("/materials/{material_id}")
async def update_material(
    circle_id: str, material_id: str, body: MaterialUpdate, current_user: CurrentUser
):
    """Update material metadata."""
    try:
        material = await kb_service.update_material(
            db, circle_id, material_id, current_user.id, body.model_dump(exclude_none=True)
        )
        if not material:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Material not found.")
        return {
            "id": material.id,
            "title": material.title,
            "description": material.description,
            "folder": material.folder,
            "updatedAt": material.updatedAt.isoformat(),
        }
    except PermissionError as e:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(e))


@router.delete("/materials/{material_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_material(circle_id: str, material_id: str, current_user: CurrentUser):
    """Delete a material."""
    try:
        deleted = await kb_service.delete_material(db, circle_id, material_id, current_user.id)
        if not deleted:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Material not found.")
    except PermissionError as e:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(e))


# ==========================================
#  KNOWLEDGE LINKS
# ==========================================


@router.post("/links", status_code=status.HTTP_201_CREATED)
async def create_knowledge_link(
    circle_id: str, body: KnowledgeLinkCreate, current_user: CurrentUser
):
    """Link a KB item to a chat group or session."""
    try:
        link = await kb_service.create_knowledge_link(
            db, circle_id, current_user.id, body.model_dump()
        )
        return {
            "id": link.id,
            "circleId": link.circleId,
            "curriculumId": link.curriculumId,
            "sectionId": link.sectionId,
            "materialId": link.materialId,
            "chatGroupId": link.chatGroupId,
            "sessionId": link.sessionId,
            "createdById": link.createdById,
            "createdAt": link.createdAt.isoformat(),
        }
    except PermissionError as e:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(e))
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))


@router.delete("/links/{link_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_knowledge_link(circle_id: str, link_id: str, current_user: CurrentUser):
    """Remove a knowledge link."""
    try:
        deleted = await kb_service.delete_knowledge_link(db, circle_id, link_id, current_user.id)
        if not deleted:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Link not found.")
    except PermissionError as e:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(e))


@router.get("/links/chat-group/{chat_group_id}")
async def list_links_for_chat_group(circle_id: str, chat_group_id: str, current_user: CurrentUser):
    """List knowledge links for a specific chat group."""
    try:
        links = await kb_service.list_links_for_chat_group(
            db, circle_id, chat_group_id, current_user.id
        )
        return {
            "links": [
                {
                    "id": link.id,
                    "circleId": link.circleId,
                    "curriculumId": link.curriculumId,
                    "sectionId": link.sectionId,
                    "materialId": link.materialId,
                    "chatGroupId": link.chatGroupId,
                    "sourceTitle": _get_link_source_title(link),
                    "sourceType": _get_link_source_type(link),
                    "createdAt": link.createdAt.isoformat(),
                }
                for link in links
            ]
        }
    except PermissionError as e:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(e))


@router.get("/links/session/{session_id}")
async def list_links_for_session(circle_id: str, session_id: str, current_user: CurrentUser):
    """List knowledge links for a specific session."""
    try:
        links = await kb_service.list_links_for_session(db, circle_id, session_id, current_user.id)
        return {
            "links": [
                {
                    "id": link.id,
                    "circleId": link.circleId,
                    "curriculumId": link.curriculumId,
                    "sectionId": link.sectionId,
                    "materialId": link.materialId,
                    "sessionId": link.sessionId,
                    "sourceTitle": _get_link_source_title(link),
                    "sourceType": _get_link_source_type(link),
                    "createdAt": link.createdAt.isoformat(),
                }
                for link in links
            ]
        }
    except PermissionError as e:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(e))


# ==========================================
#  PROGRESS
# ==========================================


@router.post("/curricula/{curriculum_id}/sections/{section_id}/complete")
async def mark_section_complete(
    circle_id: str, curriculum_id: str, section_id: str, current_user: CurrentUser
):
    """Mark a section as complete for the current user."""
    try:
        result = await kb_service.mark_section_complete(
            db, circle_id, curriculum_id, section_id, current_user.id
        )
        return result
    except PermissionError as e:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(e))
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(e))


@router.get("/curricula/{curriculum_id}/progress/me")
async def get_my_progress(circle_id: str, curriculum_id: str, current_user: CurrentUser):
    """Get the current user's progress for a curriculum."""
    try:
        progress = await kb_service.get_my_curriculum_progress(
            db, circle_id, curriculum_id, current_user.id
        )
        return progress
    except PermissionError as e:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(e))


@router.get("/curricula/{curriculum_id}/progress")
async def get_all_progress(circle_id: str, curriculum_id: str, current_user: CurrentUser):
    """Get all members' progress (OWNER/ADMIN only)."""
    try:
        progress_list = await kb_service.get_all_members_progress(
            db, circle_id, curriculum_id, current_user.id
        )
        return {
            "progress": [
                {
                    "curriculumId": p.curriculumId,
                    "userId": p.userId,
                    "userName": p.user.name if hasattr(p, "user") and p.user else None,
                    "completedSections": p.completedSections,
                    "totalSections": p.totalSections,
                    "percentage": p.percentage,
                    "completedAt": p.completedAt.isoformat() if p.completedAt else None,
                    "startedAt": p.startedAt.isoformat(),
                }
                for p in progress_list
            ]
        }
    except PermissionError as e:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(e))


# ==========================================
#  SEARCH
# ==========================================


@router.get("/search")
async def search_knowledge_base(
    circle_id: str,
    current_user: CurrentUser,
    q: str = Query(..., min_length=1, max_length=200),
):
    """Search across the knowledge base."""
    try:
        results = await kb_service.search_knowledge_base(db, circle_id, current_user.id, q)
        return {
            "curricula": [
                {
                    "id": c.id,
                    "title": c.title,
                    "description": c.description,
                    "status": str(c.status),
                    "type": "curriculum",
                }
                for c in results["curricula"]
            ],
            "materials": [
                {
                    "id": m.id,
                    "title": m.title,
                    "description": m.description,
                    "type": str(m.type),
                    "folder": m.folder,
                    "resultType": "material",
                }
                for m in results["materials"]
            ],
        }
    except PermissionError as e:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(e))


# --- Helpers ---


def _get_link_source_title(link) -> str | None:
    """Extract the title from a knowledge link's source."""
    if link.curriculum:
        return link.curriculum.title
    if link.section:
        return link.section.title
    if link.material:
        return link.material.title
    return None


def _get_link_source_type(link) -> str | None:
    """Determine the source type of a knowledge link."""
    if link.curriculumId:
        return "curriculum"
    if link.sectionId:
        return "section"
    if link.materialId:
        return "material"
    return None
