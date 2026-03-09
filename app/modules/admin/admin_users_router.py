import logging

from fastapi import APIRouter, Depends, Query

from app.core.config import Settings, get_settings
from app.core.dependencies import get_supabase
from app.core.database_client import DatabaseClient
from app.core.permissions import RequirePermission
from app.modules.admin.admin_users_service import AdminUsersService
from app.modules.admin.schemas import (
    AdminUser,
    AdminUserCreateRequest,
    AdminUserCreateResponse,
    AdminUserDetail,
    AdminUserListResponse,
    AdminUserUpdateRequest,
)
from app.modules.email.service import EmailService

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/admin/admin-users", tags=["Admin - Admin Users"])


def get_admin_users_service(
    supabase: DatabaseClient = Depends(get_supabase),
) -> AdminUsersService:
    return AdminUsersService(supabase)


@router.get("", response_model=AdminUserListResponse)
async def list_admin_users(
    limit: int = Query(50, ge=1, le=500),
    offset: int = Query(0, ge=0),
    _: AdminUser = Depends(RequirePermission("canManageAdmins")),
    service: AdminUsersService = Depends(get_admin_users_service),
):
    items, total = service.list_admins(limit=limit, offset=offset)
    return AdminUserListResponse(items=items, total=total, limit=limit, offset=offset)


@router.post("", response_model=AdminUserCreateResponse, status_code=201)
async def create_admin_user(
    body: AdminUserCreateRequest,
    admin: AdminUser = Depends(RequirePermission("canManageAdmins")),
    service: AdminUsersService = Depends(get_admin_users_service),
    settings: Settings = Depends(get_settings),
):
    admin_data, temp_password = service.create_admin(
        email=body.email, name=body.name, role=body.role
    )

    try:
        email_service = EmailService(settings)
        email_service.send(
            to_email=body.email,
            template_name="admin_invite",
            context={
                "name": body.name or body.email,
                "role": body.role.replace("_", " ").title(),
                "temporary_password": temp_password,
                "login_url": f"{settings.FRONTEND_URL}/admin/login",
            },
        )
    except Exception:
        logger.warning("Failed to send admin invite email to %s", body.email, exc_info=True)

    return AdminUserCreateResponse(admin=admin_data, temporary_password=temp_password)


@router.put("/{admin_id}", response_model=AdminUserDetail)
async def update_admin_user(
    admin_id: str,
    body: AdminUserUpdateRequest,
    admin: AdminUser = Depends(RequirePermission("canManageAdmins")),
    service: AdminUsersService = Depends(get_admin_users_service),
):
    updated = service.update_admin(
        admin_id,
        body.model_dump(exclude_none=True),
        current_admin_id=admin.id,
    )
    return updated


@router.delete("/{admin_id}")
async def delete_admin_user(
    admin_id: str,
    admin: AdminUser = Depends(RequirePermission("canManageAdmins")),
    service: AdminUsersService = Depends(get_admin_users_service),
):
    service.delete_admin(admin_id, current_admin_id=admin.id)
    return {"success": True}
