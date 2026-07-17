from fastapi import Depends, HTTPException

from app.core.dependencies import get_current_admin
from app.modules.admin.schemas import AdminUser

ROLE_PERMISSIONS: dict[str, set[str]] = {
    "master_admin": {
        "canManageAdmins",
        "canViewBusinessMetrics",
        "canEditIncentiveData",
        "canEditComparables",
        "canManageDataSources",
        "canManageEmailGating",
        "canManagePDFReports",
        "canViewPlatformEconomics",
        "canManageB2B",
    },
    "senior_admin": {
        "canViewBusinessMetrics",
        "canEditIncentiveData",
        "canEditComparables",
        "canManageDataSources",
        "canManageEmailGating",
        "canManagePDFReports",
        "canViewPlatformEconomics",
        "canManageB2B",
    },
    "data_admin": {
        "canEditIncentiveData",
        "canEditComparables",
    },
    "support_admin": {
        "canManageEmailGating",
        "canManagePDFReports",
    },
}


def has_permission(role: str, permission: str) -> bool:
    return permission in ROLE_PERMISSIONS.get(role, set())


class RequirePermission:
    """Dependency that enforces a specific permission on the admin.

    Usage: Depends(RequirePermission("canManageAdmins"))
    """

    def __init__(self, permission: str) -> None:
        self.permission = permission

    async def __call__(self, admin: AdminUser = Depends(get_current_admin)) -> AdminUser:
        if not has_permission(admin.role, self.permission):
            raise HTTPException(
                status_code=403,
                detail=f"Permission '{self.permission}' required",
            )
        return admin
