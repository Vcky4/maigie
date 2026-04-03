"""
Super-admin-only staff directory and role management.
"""

import logging

from fastapi import APIRouter, HTTPException, status

from src.dependencies import DBDep, SuperAdminUser
from src.models.cms import StaffMemberResponse, StaffMemberUpdate
from src.services.audit_service import log_admin_action

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/admin/staff", tags=["admin"])

_ALLOWED = frozenset({"SUPER_ADMIN", "CONTENT_MANAGER"})


@router.get("", response_model=list[StaffMemberResponse])
async def list_admin_staff(
    _super: SuperAdminUser,
    db: DBDep,
):
    users = await db.user.find_many(where={"role": "ADMIN"}, order={"email": "asc"})
    out: list[StaffMemberResponse] = []
    for u in users:
        raw = getattr(u, "adminStaffRole", None)
        if raw is None:
            ar = "SUPER_ADMIN"
        elif isinstance(raw, str):
            ar = raw
        else:
            ar = str(getattr(raw, "value", raw) or "SUPER_ADMIN")
        out.append(
            StaffMemberResponse(
                id=u.id,
                email=u.email,
                name=u.name,
                role=str(u.role),
                adminStaffRole=ar,
                isActive=u.isActive,
            )
        )
    return out


@router.patch("/{user_id}", response_model=StaffMemberResponse)
async def update_admin_staff_role(
    user_id: str,
    body: StaffMemberUpdate,
    admin_user: SuperAdminUser,
    db: DBDep,
):
    if body.adminStaffRole not in _ALLOWED:
        raise HTTPException(status_code=400, detail="Invalid adminStaffRole")

    user = await db.user.find_unique(where={"id": user_id})
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    if str(user.role) != "ADMIN":
        raise HTTPException(status_code=400, detail="User is not an admin")

    updated = await db.user.update(
        where={"id": user_id},
        data={"adminStaffRole": body.adminStaffRole},
    )

    await log_admin_action(
        admin_user.id,
        "update_admin_staff_role",
        "user",
        resource_id=user_id,
        details={"adminStaffRole": body.adminStaffRole},
        db_client=db,
    )

    return StaffMemberResponse(
        id=updated.id,
        email=updated.email,
        name=updated.name,
        role=str(updated.role),
        adminStaffRole=body.adminStaffRole,
        isActive=updated.isActive,
    )
