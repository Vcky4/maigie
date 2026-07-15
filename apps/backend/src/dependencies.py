"""DEPRECATED — Re-exports from src.shared.auth for backward compatibility.

Legacy services import from here. New code should import from src.shared.auth directly.
"""

from src.shared.auth.dependencies import (
    CurrentUser,
    PremiumUser,
    SpaceMemberUser as ExamPrepUser,
    StaffUser as AdminUser,
    StaffUser as StaffAdminUser,
    SuperAdminUser,
    get_current_user,
    get_staff_user as get_staff_admin_user,
    get_super_admin_user,
    require_premium,
    PAID_TIERS,
)
from src.shared.database import db

# Legacy type aliases
from typing import Annotated
from fastapi import Depends
from prisma import Prisma


async def get_db() -> Prisma:
    return db


DBDep = Annotated[Prisma, Depends(get_db)]

# Legacy settings dependency
from src.config import Settings, get_settings

SettingsDep = Annotated[Settings, Depends(get_settings)]

__all__ = [
    "CurrentUser",
    "AdminUser",
    "StaffAdminUser",
    "SuperAdminUser",
    "PremiumUser",
    "ExamPrepUser",
    "DBDep",
    "SettingsDep",
    "PAID_TIERS",
    "get_current_user",
    "get_staff_admin_user",
    "get_super_admin_user",
    "require_premium",
    "get_db",
    "db",
]
