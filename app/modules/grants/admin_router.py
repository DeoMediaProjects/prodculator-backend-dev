from fastapi import APIRouter, Depends, File, HTTPException, Query, UploadFile

from app.core.database_client import DatabaseClient
from app.core.dependencies import get_supabase
from app.core.permissions import RequirePermission
from app.core.schemas import SuccessResponse
from app.modules.admin.schemas import (
    AdminListResponse,
    AdminUpsertRequest,
    AdminUser,
    PendingChangeResponse,
    SyncSettingsResponse,
    SyncSettingsUpdateRequest,
    SyncStatusResponse,
)
from app.modules.grants.service import GrantsService

router = APIRouter(prefix="/api/admin/grants", tags=["Admin - Grants"])


def get_grants_service(supabase: DatabaseClient = Depends(get_supabase)) -> GrantsService:
    return GrantsService(supabase)


@router.get("", response_model=AdminListResponse)
async def list_grants_admin(
    limit: int = Query(50, ge=1, le=500),
    offset: int = Query(0, ge=0),
    _: AdminUser = Depends(RequirePermission("canEditIncentiveData")),
    service: GrantsService = Depends(get_grants_service),
):
    try:
        items, total = service.list_for_admin(limit=limit, offset=offset)
        return AdminListResponse(items=items, total=total, limit=limit, offset=offset)
    except Exception:
        raise HTTPException(status_code=500, detail="Failed to fetch grants")


@router.post("/bulk-import", response_model=dict)
async def bulk_import_grants_admin(
    file: UploadFile = File(...),
    _: AdminUser = Depends(RequirePermission("canEditIncentiveData")),
    service: GrantsService = Depends(get_grants_service),
):
    try:
        content = await file.read()
        csv_text = content.decode("utf-8")
    except UnicodeDecodeError:
        raise HTTPException(status_code=400, detail="Invalid file encoding — expected UTF-8 CSV")
    try:
        return service.bulk_import(csv_text)
    except Exception:
        raise HTTPException(status_code=400, detail="Failed to import grants")


@router.post("/sync", response_model=dict)
async def trigger_grants_sync(
    _: AdminUser = Depends(RequirePermission("canEditIncentiveData")),
    service: GrantsService = Depends(get_grants_service),
):
    try:
        return service.trigger_sync()
    except Exception:
        raise HTTPException(status_code=500, detail="Failed to trigger sync")


@router.get("/sync-status", response_model=SyncStatusResponse)
async def get_sync_status(
    _: AdminUser = Depends(RequirePermission("canEditIncentiveData")),
    service: GrantsService = Depends(get_grants_service),
):
    try:
        return SyncStatusResponse(**service.get_sync_status())
    except Exception:
        raise HTTPException(status_code=500, detail="Failed to fetch sync status")


@router.get("/pending-changes", response_model=list[PendingChangeResponse])
async def get_pending_changes(
    _: AdminUser = Depends(RequirePermission("canEditIncentiveData")),
    service: GrantsService = Depends(get_grants_service),
):
    try:
        return service.get_pending_changes()
    except Exception:
        raise HTTPException(status_code=500, detail="Failed to fetch pending changes")


@router.post("/pending-changes/{change_id}/approve", response_model=PendingChangeResponse)
async def approve_pending_change(
    change_id: str,
    admin: AdminUser = Depends(RequirePermission("canEditIncentiveData")),
    service: GrantsService = Depends(get_grants_service),
):
    try:
        return service.approve_change(change_id, admin.id)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception:
        raise HTTPException(status_code=400, detail="Failed to approve change")


@router.post("/pending-changes/{change_id}/reject", response_model=PendingChangeResponse)
async def reject_pending_change(
    change_id: str,
    admin: AdminUser = Depends(RequirePermission("canEditIncentiveData")),
    service: GrantsService = Depends(get_grants_service),
):
    try:
        return service.reject_change(change_id, admin.id)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception:
        raise HTTPException(status_code=400, detail="Failed to reject change")


@router.get("/sync-settings", response_model=SyncSettingsResponse)
async def get_sync_settings(
    _: AdminUser = Depends(RequirePermission("canEditIncentiveData")),
    service: GrantsService = Depends(get_grants_service),
):
    try:
        return service.get_sync_settings()
    except Exception:
        raise HTTPException(status_code=500, detail="Failed to fetch sync settings")


@router.patch("/sync-settings", response_model=SyncSettingsResponse)
async def update_sync_settings(
    body: SyncSettingsUpdateRequest,
    _: AdminUser = Depends(RequirePermission("canEditIncentiveData")),
    service: GrantsService = Depends(get_grants_service),
):
    try:
        payload = body.model_dump(exclude_none=True)
        return service.update_sync_settings(payload)
    except Exception:
        raise HTTPException(status_code=400, detail="Failed to update sync settings")


@router.post("", response_model=dict)
async def create_grant_admin(
    body: AdminUpsertRequest,
    _: AdminUser = Depends(RequirePermission("canEditIncentiveData")),
    service: GrantsService = Depends(get_grants_service),
):
    try:
        return service.create(body.payload)
    except Exception:
        raise HTTPException(status_code=400, detail="Failed to create grant")


@router.patch("/{item_id}", response_model=dict)
async def update_grant_admin(
    item_id: str,
    body: AdminUpsertRequest,
    _: AdminUser = Depends(RequirePermission("canEditIncentiveData")),
    service: GrantsService = Depends(get_grants_service),
):
    try:
        return service.update(item_id, body.payload)
    except Exception:
        raise HTTPException(status_code=400, detail="Failed to update grant")


@router.delete("/{item_id}", response_model=SuccessResponse)
async def delete_grant_admin(
    item_id: str,
    _: AdminUser = Depends(RequirePermission("canEditIncentiveData")),
    service: GrantsService = Depends(get_grants_service),
):
    try:
        service.delete(item_id)
        return SuccessResponse(message="grant deleted")
    except Exception:
        raise HTTPException(status_code=400, detail="Failed to delete grant")
